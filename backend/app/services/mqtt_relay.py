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
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from backend.app.services import mqtt_ha_discovery as ha_discovery
from backend.app.utils.paho_teardown import retire_paho_client

logger = logging.getLogger(__name__)


# Fields the printer status topic takes from the same dict the REST status and the
# WebSocket send, so a subscriber reads them in the shape it already knows (#3401).
# Internal setting: the prefixes whose discovery configs are still to be swept (#3724).
STALE_SCOPES_SETTING = "mqtt_ha_discovery_stale_scopes"

_STATUS_FIELDS_FROM_STATE = ("door_open", "active_extruder", "tray_now", "ams", "vt_tray", "hms_errors")


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
        self._last_printer_connected: dict[int, bool] = {}  # printer_id -> `connected` in the last publish
        self._smart_plug_service = None  # Lazy import to avoid circular dependency
        self._settings: dict = {}  # Store settings for smart plug service
        self._disconnection_event: threading.Event | None = None
        # Home Assistant MQTT discovery (#3724)
        self.ha_discovery_enabled = False
        self.ha_discovery_prefix = ha_discovery.DEFAULT_DISCOVERY_PREFIX
        self._external_url = ""
        # Guards the three below: the paho thread reads and writes them too.
        self._discovery_lock = threading.Lock()
        self._discovery_sent: dict[str, dict] = {}  # discovery topic -> the config published on it
        # Our retained configs as the broker holds them, which is what is left
        # of them after a restart: cleared along with _discovery_sent.
        self._discovery_seen: set[str] = set()
        self._discovery_signatures: dict[str, tuple] = {}  # printer serial -> what its configs were built from
        # (discovery prefix, topic prefix) pairs whose configs must go: published
        # under prefixes that are no longer in use, possibly while the broker was
        # unreachable. Swept on every connect, and kept in the settings table so
        # a restart before the broker is back does not forget them.
        self._stale_scopes: set[tuple[str, str]] = set()
        self._stale_scopes_loaded = False

    async def configure(self, settings: dict) -> bool:
        """Configure MQTT connection from settings.

        Returns True if connection was successful or MQTT is disabled.
        """
        enabled = settings.get("mqtt_enabled", False)
        topic_prefix = settings.get("mqtt_topic_prefix", "bambuddy")
        discovery_prefix = ha_discovery.normalize_prefix(
            settings.get("mqtt_ha_discovery_prefix"), ha_discovery.DEFAULT_DISCOVERY_PREFIX
        )
        discovery_enabled = bool(enabled and settings.get("mqtt_ha_discovery_enabled", False))
        if discovery_enabled and ha_discovery.normalize_prefix(topic_prefix) == discovery_prefix:
            # The settings route refuses this; a restored or hand-edited value
            # must not put printer status under Home Assistant's discovery topics.
            logger.warning(
                "Home Assistant discovery is off: the topic prefix and the discovery prefix are both %r",
                discovery_prefix,
            )
            discovery_enabled = False

        # What Home Assistant was told so far is wrong from here on: clear it
        # over the connection that is still up, before the prefixes change.
        if not self._stale_scopes_loaded:
            self._stale_scopes |= await self._load_stale_scopes()
            self._stale_scopes_loaded = True
        stale_before = set(self._stale_scopes)
        old_scope = (self.ha_discovery_prefix, self.topic_prefix)
        if self.ha_discovery_enabled and (not discovery_enabled or (discovery_prefix, topic_prefix) != old_scope):
            await self._clear_discovery()
            # The clear above reaches nothing while the broker is down: sweep
            # the old prefixes once it is back.
            self._stale_scopes.add(old_scope)
        # Back to prefixes used before: what is there now is ours to keep.
        if discovery_enabled:
            self._stale_scopes.discard((discovery_prefix, topic_prefix))
        if self._stale_scopes != stale_before:
            await self._save_stale_scopes()

        self._loop = asyncio.get_running_loop()
        self.enabled = enabled
        self.ha_discovery_enabled = discovery_enabled
        self.ha_discovery_prefix = discovery_prefix
        self._external_url = (settings.get("external_url") or "").strip()
        self._settings = settings  # Store for smart plug service
        # Panda Breath heaters (#1466) use this broker too; it decides on its own
        # whether to connect, so it is told on every path below, including a
        # broker that was cleared.
        await self._configure_panda_breath(settings)

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
        self.topic_prefix = topic_prefix
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

    @staticmethod
    async def _configure_panda_breath(settings: dict):
        try:
            from backend.app.services.panda_breath import panda_breath_service

            await panda_breath_service.configure(settings)
        except Exception as e:
            logger.error("Failed to configure Panda Breath: %s", e)

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

            # Last Will: the broker publishes this when the connection drops without a
            # clean disconnect (crash, killed container), which would otherwise leave the
            # retained "online" in place for good (#3401). It replaces that retained message.
            self.client.will_set(f"{self.topic_prefix}/status", json.dumps({"status": "offline"}), qos=1, retain=True)

            # Set up callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

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
            # Here rather than once after connecting: paho reconnects on its
            # own, and a broker that restarted has lost the subscriptions.
            self._subscribe_discovery(client)
            if self.ha_discovery_enabled:
                self._resend_discovery()
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
                retire_paho_client(self.client, "relay")
            except Exception as e:
                logger.debug("MQTT disconnect error (ignored): %s", e)
            finally:
                self.client = None
                self.connected = False

    def _publish_status(self, status: str):
        """Publish BamBuddy status (online/offline)."""
        self._publish(
            f"{self.topic_prefix}/status",
            {"status": status, "timestamp": datetime.now(timezone.utc).isoformat()},
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

    # =========================================================================
    # Home Assistant discovery (#3724)
    # =========================================================================

    def _publish_clear(self, topic: str):
        """Remove a retained message: an empty retained payload on its topic."""
        if not self.client or not self.connected:
            return None
        try:
            with self._lock:
                return self.client.publish(topic, b"", qos=1, retain=True)
        except Exception as e:
            logger.debug("MQTT publish error: %s", e)
            return None

    # How long a prefix that is only swept stays subscribed: long enough for the
    # broker to send its retained configs.
    SWEEP_SECONDS = 10.0

    def _sweep_scopes(self) -> set[tuple[str, str]]:
        """The (discovery prefix, topic prefix) pairs whose configs of ours must go."""
        scopes = set(self._stale_scopes)
        if not self.ha_discovery_enabled:
            # Turned off, possibly while the broker was down or before a restart.
            scopes.add((self.ha_discovery_prefix, self.topic_prefix))
        return scopes

    def _subscribe_discovery(self, client: mqtt.Client):
        """Subscribe to what discovery reads, and to the configs to sweep.

        A prefix that is only swept is unsubscribed again after a while: with
        discovery off there is no reason to keep receiving every device config
        other integrations keep on the broker.
        """
        active = {self.ha_discovery_prefix} if self.ha_discovery_enabled else set()
        sweep_only = {prefix for prefix, _ in self._sweep_scopes()} - active
        topics = [(f"{prefix}/device/+/config", 1) for prefix in sorted(active | sweep_only)]
        if self.ha_discovery_enabled:
            topics.append((f"{self.ha_discovery_prefix}/status", 1))
        if not topics:
            return
        try:
            client.subscribe(topics)
        except Exception as e:
            logger.debug("MQTT discovery subscribe error: %s", e)
            return
        if sweep_only:
            timer = threading.Timer(
                self.SWEEP_SECONDS,
                self._end_sweep,
                args=(client, [f"{prefix}/device/+/config" for prefix in sorted(sweep_only)]),
            )
            timer.daemon = True
            timer.start()

    def _end_sweep(self, client: mqtt.Client, topics: list[str]):
        if client is not self.client:
            return
        active = f"{self.ha_discovery_prefix}/device/+/config" if self.ha_discovery_enabled else None
        topics = [topic for topic in topics if topic != active]
        try:
            if topics:
                client.unsubscribe(topics)
        except Exception as e:
            logger.debug("MQTT discovery unsubscribe error: %s", e)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage):
        """Home Assistant's birth message, and retained discovery configs of ours."""
        try:
            topic = message.topic
            if self.ha_discovery_enabled and topic == f"{self.ha_discovery_prefix}/status":
                # Home Assistant (re)started and has forgotten every device.
                if message.payload.decode("utf-8", errors="replace").strip() == "online":
                    self._resend_discovery()
                return
            if not topic.endswith("/config") or "/device/" not in topic:
                return
            discovery_prefix = topic[: topic.rindex("/device/")]
            if ha_discovery.device_id_from_topic(discovery_prefix, topic) is None:
                return
            if not message.payload:
                with self._discovery_lock:
                    self._discovery_seen.discard(topic)
                return
            config = json.loads(message.payload)
            # Whose it is, from what it reads: <topic prefix>/printers/<serial>/status.
            # A Bambuddy with another topic prefix reads other topics.
            state_topic = config.get("state_topic") if isinstance(config, dict) else None
            if (
                not isinstance(state_topic, str)
                or not state_topic.endswith("/status")
                or "/printers/" not in state_topic
            ):
                return
            topic_prefix, serial = state_topic[: -len("/status")].rsplit("/printers/", 1)
            if not serial or "/" in serial:
                return

            if (discovery_prefix, topic_prefix) in self._sweep_scopes():
                self._publish_clear(topic)
                with self._discovery_lock:
                    self._discovery_seen.discard(topic)
                return
            if not self.ha_discovery_enabled or (discovery_prefix, topic_prefix) != (
                self.ha_discovery_prefix,
                self.topic_prefix,
            ):
                return
            with self._discovery_lock:
                self._discovery_seen.add(topic)
                if topic in self._discovery_sent:
                    return
                # Left from before a restart, and its printer may have been
                # announced already: have the next status compare again, so an
                # AMS that went meanwhile is cleared.
                self._discovery_signatures.clear()
            # Possibly a printer deleted while the broker was unreachable, which
            # no status will ever come for: ask the database.
            if self._loop is not None and not self._loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._clear_if_orphaned(topic, serial), self._loop)
        except Exception as e:
            logger.debug("MQTT discovery message ignored: %s", e)

    @staticmethod
    async def _load_stale_scopes() -> set[tuple[str, str]]:
        try:
            from sqlalchemy import select

            from backend.app.core import database
            from backend.app.models.settings import Settings

            async with database.async_session() as db:
                value = await db.scalar(select(Settings.value).where(Settings.key == STALE_SCOPES_SETTING))
            pairs = json.loads(value) if value else []
            return {
                (str(pair[0]), str(pair[1]))
                for pair in pairs
                if isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair)
            }
        except Exception as e:
            logger.debug("Could not read the stale Home Assistant discovery prefixes: %s", e)
            return set()

    async def _save_stale_scopes(self):
        try:
            from backend.app.core import database
            from backend.app.core.db_dialect import upsert_setting
            from backend.app.models.settings import Settings

            async with database.async_session() as db:
                await upsert_setting(db, Settings, STALE_SCOPES_SETTING, json.dumps(sorted(self._stale_scopes)))
                await db.commit()
        except Exception as e:
            logger.debug("Could not store the stale Home Assistant discovery prefixes: %s", e)

    @staticmethod
    async def _printer_exists(serial: str) -> bool | None:
        """Whether a printer with this serial is in the database; None if it can't be told."""
        try:
            from sqlalchemy import func, select

            from backend.app.core import database
            from backend.app.models.printer import Printer

            async with database.async_session() as db:
                count = await db.scalar(
                    select(func.count())
                    .select_from(Printer)
                    .where(func.upper(func.trim(Printer.serial_number)) == serial.strip().upper())
                )
            return bool(count)
        except Exception as e:
            logger.debug("Could not look up printer %s: %s", serial, e)
            return None

    async def _clear_if_orphaned(self, topic: str, serial: str):
        """Clear a retained config of ours whose printer is not in the database."""
        if await self._printer_exists(serial) is not False:
            return
        with self._discovery_lock:
            if topic in self._discovery_sent:
                return
            self._discovery_seen.discard(topic)
        logger.info("Removing Home Assistant discovery config of deleted printer %s", serial)
        self._publish_clear(topic)

    def _resend_discovery(self):
        """Publish every discovery config again, as it was last published."""
        with self._discovery_lock:
            configs = list(self._discovery_sent.items())
        for topic, config in configs:
            self._publish(topic, config, retain=True)

    async def _clear_discovery(self, printer_serial: str | None = None):
        """Remove our discovery configs from the broker, so Home Assistant drops
        the devices: all of them, or one printer's with its AMS units."""
        prefix = self.ha_discovery_prefix
        with self._discovery_lock:
            topics = set(self._discovery_sent) | self._discovery_seen
            if printer_serial is not None:
                # Also when nothing is known of it: its config may be on the
                # broker from before a restart, the subscription not answered yet.
                topics.add(ha_discovery.discovery_topic(prefix, ha_discovery.printer_device_id(printer_serial)))
                topics = {
                    topic
                    for topic in topics
                    if ha_discovery.belongs_to_printer(
                        ha_discovery.device_id_from_topic(prefix, topic) or "", printer_serial
                    )
                }
                self._discovery_signatures.pop(printer_serial, None)
            else:
                self._discovery_signatures.clear()
            for topic in topics:
                self._discovery_sent.pop(topic, None)
                self._discovery_seen.discard(topic)
        # AMS units before their printer, so no device is left pointing at one that is gone.
        pending = [self._publish_clear(topic) for topic in sorted(topics, key=len, reverse=True)]
        pending = [info for info in pending if info is not None]
        if not pending:
            return

        def wait():
            # configure() disconnects right after this: let the broker have them first.
            deadline = time.monotonic() + 2.0
            for info in pending:
                try:
                    info.wait_for_publish(timeout=max(0.0, deadline - time.monotonic()))
                except Exception:
                    return

        await asyncio.to_thread(wait)

    async def _sync_discovery(
        self,
        printer_name: str,
        printer_serial: str,
        printer_model: str | None,
        snapshot: dict,
        temperatures: dict | None,
    ):
        """Publish a printer's discovery configs when they are new or have changed.

        Called with every status: a rename, a firmware update and an AMS added
        or removed all change what the configs are built from. An AMS that is
        gone has its config cleared.
        """
        units = snapshot.get("ams") or []
        signature = (
            self.ha_discovery_prefix,
            self.topic_prefix,
            self._external_url,
            printer_name,
            printer_model,
            snapshot.get("firmware_version"),
            "nozzle_2" in (temperatures or {}),
            tuple(
                (
                    unit.get("id"),
                    unit.get("is_ams_ht"),
                    unit.get("module_type"),
                    unit.get("ams_type"),
                    unit.get("serial_number"),
                    unit.get("sw_ver"),
                    tuple(tray.get("id") for tray in unit.get("tray") or []),
                )
                for unit in units
            ),
            tuple(tray.get("id") for tray in snapshot.get("vt_tray") or []),
        )
        with self._discovery_lock:
            if self._discovery_signatures.get(printer_serial) == signature:
                return

        configs = ha_discovery.build_configs(
            discovery_prefix=self.ha_discovery_prefix,
            topic_prefix=self.topic_prefix,
            serial=printer_serial,
            name=printer_name,
            model=printer_model,
            snapshot=snapshot,
            temperatures=temperatures,
            external_url=self._external_url or None,
        )
        with self._discovery_lock:
            self._discovery_signatures[printer_serial] = signature
            changed = {topic: config for topic, config in configs.items() if self._discovery_sent.get(topic) != config}
            gone = [
                topic
                for topic in set(self._discovery_sent) | self._discovery_seen
                if topic not in configs
                and ha_discovery.belongs_to_printer(
                    ha_discovery.device_id_from_topic(self.ha_discovery_prefix, topic) or "", printer_serial
                )
            ]
            self._discovery_sent.update(changed)
            for topic in gone:
                self._discovery_sent.pop(topic, None)
                self._discovery_seen.discard(topic)
        # The printer first: its AMS units name it as their via_device.
        for topic, config in changed.items():
            self._publish(topic, config, retain=True)
        for topic in gone:
            self._publish_clear(topic)

    async def on_printer_removed(self, printer_serial: str):
        """A printer was deleted: remove it and its AMS units from Home Assistant.

        With the broker unreachable this only forgets them, so a reconnect does
        not announce them again; their retained configs are cleared once the
        broker is back, when they arrive over the subscription (_on_message).
        """
        if not self.ha_discovery_enabled:
            return
        await self._clear_discovery(printer_serial)

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

    async def on_printer_status(
        self,
        printer_id: int,
        state: Any,
        printer_name: str,
        printer_serial: str,
        awaiting_plate_clear: bool = False,
        printer_model: str | None = None,
    ):
        """Publish printer status change (throttled to 1 update/sec per printer).

        A change of ``connected`` is never throttled: the update that says a printer
        dropped is retained, and held back it would leave the previous status
        standing for as long as the printer stays quiet (#3401).
        """
        if not self.enabled or not self.connected:
            return

        # Throttle status updates to avoid flooding MQTT broker
        now = time.time()
        last_publish = self._last_printer_status.get(printer_id, 0)
        last_connected = self._last_printer_connected.get(printer_id)
        connected_changed = last_connected is not None and last_connected != state.connected
        if not connected_changed and now - last_publish < self.STATUS_THROTTLE_SECONDS:
            return  # Skip this update, too soon since last publish
        self._last_printer_status[printer_id] = now
        self._last_printer_connected[printer_id] = state.connected

        from backend.app.services.printer_manager import printer_state_to_dict

        snapshot = printer_state_to_dict(state, printer_id, printer_model)

        # Build status payload from PrinterState
        payload = {
            "printer_id": printer_id,
            "printer_name": printer_name,
            "printer_serial": printer_serial,
            # For the device Home Assistant discovery describes (#3724).
            "model": printer_model,
            "firmware_version": snapshot["firmware_version"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connected": state.connected,
            "state": state.state,
            # What the printer is doing within that state, as the printer card names it.
            "stg_cur_name": snapshot["stg_cur_name"],
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
            "left_aux_fan_speed": state.left_aux_fan_speed,
            "exhaust_fan_present": state.exhaust_fan_present,
            **{field: snapshot[field] for field in _STATUS_FIELDS_FROM_STATE},
            # Bambuddy-side gate, not printer telemetry (#2525). Mirrors what the
            # Web UI already receives via printer_state_to_dict, so an external
            # automation can tell "finished" from "finished and still waiting for
            # someone to clear the bed". Edge changes are also published on
            # printers/{serial}/plate_clear — this topic only refreshes when the
            # printer pushes telemetry, which stops entirely after Auto Off.
            "awaiting_plate_clear": awaiting_plate_clear,
        }

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/status",
            payload,
            retain=True,
        )

        # Only once the printer has reported: before that its AMS units are not
        # known, and Home Assistant would be told of a printer without any.
        if self.ha_discovery_enabled and state.connected and state.state != "unknown":
            try:
                await self._sync_discovery(printer_name, printer_serial, printer_model, snapshot, state.temperatures)
            except Exception as e:
                logger.warning("Home Assistant discovery failed for printer %s: %s", printer_serial, e)

    async def on_plate_clear_state(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        awaiting: bool,
    ):
        """Publish the plate-clear gate as it flips (#2525).

        Retained, unlike the other per-printer event topics, because this is a
        *state* an automation needs on subscribe rather than a moment it might
        have missed. The status topic carries the same field, but only refreshes
        when the printer pushes telemetry — after Auto Off cycles the printer the
        retained status payload would sit at ``awaiting_plate_clear: false``
        indefinitely while the gate is in fact still up.
        """
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/plate_clear",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "awaiting": awaiting,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
        unit: str,
    ):
        """Publish that a maintenance item is due or nearly due.

        current_value is the hours (or days) since it was last done, threshold
        its interval, and unit says which: "hours" or "days".
        """
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
                "unit": unit,
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Run Events (archive redesign: each print of an archive is a run)
    # =========================================================================

    @staticmethod
    def _run_payload(run, archive, printer_name: str | None) -> dict:
        def iso(moment):
            # Stored as naive UTC: say so.
            if moment is None:
                return None
            return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat()

        return {
            "run_id": run.id,
            "archive_id": run.archive_id,
            "print_name": run.print_name or (archive.print_name if archive is not None else None),
            "printer_id": run.printer_id,
            "printer_name": printer_name or run.printer_name,
            "plate_id": run.plate_id,
            "status": run.status,
            "started_at": iso(run.started_at),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def on_run_started(self, run, archive, printer_serial: str, printer_name: str | None):
        """Publish a print's run opened at its start: the print of an archive,
        which the print and archive events above do not name yet."""
        if not self.enabled or not self.connected:
            return
        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/run/started",
            self._run_payload(run, archive, printer_name),
        )

    async def on_run_completed(self, run, archive, printer_serial: str, printer_name: str | None):
        """Publish a print's run as its end wrote it: outcome and figures."""
        if not self.enabled or not self.connected:
            return
        payload = self._run_payload(run, archive, printer_name)
        completed_at = run.completed_at
        if completed_at is not None and completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        payload.update(
            {
                "completed_at": completed_at.isoformat() if completed_at is not None else None,
                "duration_seconds": run.duration_seconds,
                # Not measured: an end found at a reconnect, its time the
                # estimate or nothing (0).
                "duration_unmeasured": bool(run.duration_unmeasured),
                "failure_reason": run.failure_reason,
                "filament_used_grams": run.filament_used_grams,
                "cost": run.cost,
                "energy_kwh": run.energy_kwh,
            }
        )
        self._publish(f"{self.topic_prefix}/printers/{printer_serial}/run/completed", payload)

    async def on_run_discarded(self, run, printer_serial: str, printer_name: str | None):
        """Publish a run dropped without an end: its print ended unseen (a new
        print started on the printer) or its archive was deleted. Its id is
        gone after this."""
        if not self.enabled or not self.connected:
            return
        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/run/discarded",
            self._run_payload(run, None, printer_name),
        )

    # =========================================================================
    # Filament/Spoolman Events
    # =========================================================================

    async def on_filament_low(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        slot: str,
        remaining_percent: int,
        color: str | None,
        spool_id: int,
    ):
        """Publish that the spool in an AMS slot crossed its low-stock threshold.

        Sent once per crossing, at the same point as the Low Filament
        notification (print_scheduler._emit_filament_low).
        """
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/filament/low",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "slot": slot,
                "remaining_percent": remaining_percent,
                "color": color,
                "spool_id": spool_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_smart_plug_energy(
        self,
        plug_id: int,
        plug_name: str,
        power: float | None,
        energy_today: float | None,
        energy_total: float | None,
    ):
        """Publish a plug's power and energy; a value the plug doesn't report is null."""
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


# Global instance
mqtt_relay = MQTTRelayService()
