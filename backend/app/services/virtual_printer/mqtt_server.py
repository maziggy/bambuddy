"""MQTT broker for virtual printer.

Implements an MQTT broker that accepts connections from slicers,
authenticates with the configured access code, and logs print commands.
"""

import asyncio
import json
import logging
import ssl
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Default MQTT port for Bambu printers (MQTT over TLS)
MQTT_PORT = 8883


class VirtualPrinterMQTTServer:
    """MQTT broker that accepts connections from slicers.

    This is a minimal MQTT broker implementation that:
    - Accepts TLS connections on port 8883
    - Authenticates with username 'bblp' and the configured access code
    - Receives print commands on device/{serial}/request
    - Can publish status on device/{serial}/report
    """

    def __init__(
        self,
        serial: str,
        access_code: str,
        cert_path: Path,
        key_path: Path,
        port: int = MQTT_PORT,
        on_print_command: Callable[[str, dict], None] | None = None,
    ):
        """Initialize the MQTT server.

        Args:
            serial: Virtual printer serial number
            access_code: Password for authentication
            cert_path: Path to TLS certificate
            key_path: Path to TLS private key
            port: Port to listen on (default 8883)
            on_print_command: Callback when print command received (filename, data)
        """
        self.serial = serial
        self.access_code = access_code
        self.cert_path = cert_path
        self.key_path = key_path
        self.port = port
        self.on_print_command = on_print_command
        self._running = False
        self._broker = None
        self._broker_task = None

    async def start(self) -> None:
        """Start the MQTT broker."""
        if self._running:
            return

        # Try to import amqtt
        try:
            from amqtt.broker import Broker
        except ImportError:
            logger.error("amqtt not installed. Run: pip install amqtt")
            return

        logger.info(f"Starting virtual printer MQTT broker on port {self.port}")

        # Build broker configuration
        config = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"0.0.0.0:{self.port}",
                    "ssl": "on",
                    "certfile": str(self.cert_path),
                    "keyfile": str(self.key_path),
                },
            },
            "auth": {
                "allow-anonymous": False,
                "plugins": ["auth_custom"],
            },
            "topic-check": {
                "enabled": False,  # Allow any topic
            },
        }

        try:
            self._running = True

            # Create and start broker
            self._broker = Broker(config)

            # Register custom auth plugin
            self._broker.plugins_manager.plugins_handlers["auth_custom"] = self._authenticate

            # Start the broker
            await self._broker.start()
            logger.info(f"MQTT broker started on port {self.port}")

            # Keep running
            while self._running:
                await asyncio.sleep(1)

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error(f"MQTT port {self.port} is already in use")
            else:
                logger.error(f"MQTT broker error: {e}")
        except asyncio.CancelledError:
            logger.debug("MQTT broker task cancelled")
        except Exception as e:
            logger.error(f"MQTT broker error: {e}")
        finally:
            await self.stop()

    async def _authenticate(self, session) -> bool:
        """Authenticate MQTT connection.

        Args:
            session: MQTT session with username/password

        Returns:
            True if authentication successful
        """
        username = getattr(session, "username", None)
        password = getattr(session, "password", None)

        # Bambu slicers use 'bblp' as username and access code as password
        if username == "bblp" and password == self.access_code:
            logger.debug(f"MQTT client authenticated from {session.remote_address}")
            return True

        logger.warning(f"MQTT auth failed for user '{username}' from {session.remote_address}")
        return False

    async def stop(self) -> None:
        """Stop the MQTT broker."""
        logger.info("Stopping MQTT broker")
        self._running = False

        if self._broker:
            try:
                await self._broker.shutdown()
            except Exception as e:
                logger.debug(f"Error shutting down MQTT broker: {e}")
            self._broker = None


class SimpleMQTTServer:
    """Simplified MQTT server using raw sockets.

    This is a fallback implementation that handles basic MQTT protocol
    without requiring the amqtt library. It's less feature-complete but
    more lightweight.
    """

    def __init__(
        self,
        serial: str,
        access_code: str,
        cert_path: Path,
        key_path: Path,
        port: int = MQTT_PORT,
        on_print_command: Callable[[str, dict], None] | None = None,
    ):
        self.serial = serial
        self.access_code = access_code
        self.cert_path = cert_path
        self.key_path = key_path
        self.port = port
        self.on_print_command = on_print_command
        self._running = False
        self._server = None
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._status_push_task: asyncio.Task | None = None
        self._sequence_id = 0

    async def start(self) -> None:
        """Start the MQTT server."""
        if self._running:
            return

        logger.info(f"Starting simple MQTT server on port {self.port}")

        # Create SSL context with Bambu-compatible settings
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(str(self.cert_path), str(self.key_path))
        # Match Bambu printer behavior - accept any client
        ssl_context.verify_mode = ssl.CERT_NONE
        # Allow TLS 1.2 for broader compatibility (some slicers may not support 1.3)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Disable hostname checking
        ssl_context.check_hostname = False

        # Log certificate info
        import subprocess

        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", str(self.cert_path), "-noout", "-subject", "-issuer"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            logger.info(f"MQTT SSL cert info: {result.stdout.strip()}")
        except Exception:
            pass

        logger.info(f"MQTT SSL context: TLS 1.2+, cert={self.cert_path}")

        try:
            self._running = True

            # Wrapper to log ALL connection attempts including SSL errors
            async def connection_handler(reader, writer):
                try:
                    addr = writer.get_extra_info("peername")
                    ssl_obj = writer.get_extra_info("ssl_object")
                    if ssl_obj:
                        logger.info(
                            f"MQTT TLS connection from {addr} - cipher={ssl_obj.cipher()}, version={ssl_obj.version()}"
                        )
                    else:
                        logger.info(f"MQTT connection from {addr} (no TLS?)")
                    await self._handle_client(reader, writer)
                except ssl.SSLError as e:
                    logger.error(f"MQTT SSL error: {e}")
                except Exception as e:
                    logger.error(f"MQTT connection handler error: {e}")

            # Custom protocol factory to log raw connection attempts
            logger.info("Setting up MQTT server with SSL error handling...")

            # Add SSL handshake error callback
            def handle_ssl_error(loop, context):
                exception = context.get("exception")
                message = context.get("message", "")
                if "ssl" in str(exception).lower() or "ssl" in message.lower():
                    logger.error(f"SSL error: {message} - {exception}")
                else:
                    logger.debug(f"Asyncio error: {message}")

            asyncio.get_event_loop().set_exception_handler(handle_ssl_error)

            self._server = await asyncio.start_server(
                connection_handler,
                "0.0.0.0",
                self.port,
                ssl=ssl_context,
            )

            logger.info(f"Simple MQTT server listening on port {self.port}")

            # Start periodic status push task
            self._status_push_task = asyncio.create_task(self._periodic_status_push())

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.error(f"MQTT port {self.port} is already in use")
            else:
                logger.error(f"MQTT server error: {e}")
        except asyncio.CancelledError:
            logger.debug("MQTT server task cancelled")
        except Exception as e:
            logger.error(f"MQTT server error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the MQTT server."""
        logger.info("Stopping simple MQTT server")
        self._running = False

        # Stop periodic status push
        if self._status_push_task:
            self._status_push_task.cancel()
            try:
                await self._status_push_task
            except asyncio.CancelledError:
                pass
            self._status_push_task = None

        # Close all client connections (iterate over copy to avoid modification during iteration)
        for _client_id, writer in list(self._clients.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()

        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    async def _periodic_status_push(self) -> None:
        """Send periodic status updates to all connected clients."""
        logger.info("Starting periodic status push task")
        while self._running:
            try:
                await asyncio.sleep(1)  # Push every 1 second like real printers

                # Send status to all connected clients
                disconnected = []
                for client_id, writer in list(self._clients.items()):
                    try:
                        if writer.is_closing():
                            disconnected.append(client_id)
                            continue
                        await self._send_status_report(writer)
                    except Exception as e:
                        logger.debug(f"Failed to push status to {client_id}: {e}")
                        disconnected.append(client_id)

                # Remove disconnected clients
                for client_id in disconnected:
                    self._clients.pop(client_id, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic status push error: {e}")

        logger.info("Periodic status push task stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle an MQTT client connection."""
        addr = writer.get_extra_info("peername")
        client_id = f"{addr[0]}:{addr[1]}" if addr else "unknown"
        logger.info(f"MQTT client connected: {client_id}")

        authenticated = False

        try:
            while self._running:
                # Read MQTT fixed header
                try:
                    header = await asyncio.wait_for(reader.read(1), timeout=60)
                except TimeoutError:
                    break

                if not header:
                    break

                packet_type = (header[0] & 0xF0) >> 4

                # Read remaining length
                remaining_length = await self._read_remaining_length(reader)
                if remaining_length is None:
                    break

                # Read payload
                payload = await reader.read(remaining_length) if remaining_length > 0 else b""

                # Handle packet types
                if packet_type == 1:  # CONNECT
                    authenticated = await self._handle_connect(payload, writer)
                    if not authenticated:
                        break
                    # Register client for periodic status pushes
                    self._clients[client_id] = writer
                elif packet_type == 3:  # PUBLISH
                    if authenticated:
                        await self._handle_publish(header[0], payload, writer)
                elif packet_type == 8:  # SUBSCRIBE
                    if authenticated:
                        await self._handle_subscribe(payload, writer)
                elif packet_type == 12:  # PINGREQ
                    # Send PINGRESP
                    writer.write(bytes([0xD0, 0x00]))
                    await writer.drain()
                elif packet_type == 14:  # DISCONNECT
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"MQTT client error: {e}")
        finally:
            logger.debug(f"MQTT client disconnected: {client_id}")
            if client_id in self._clients:
                del self._clients[client_id]
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_remaining_length(self, reader: asyncio.StreamReader) -> int | None:
        """Read MQTT remaining length (variable byte integer)."""
        multiplier = 1
        value = 0

        for _ in range(4):
            try:
                byte = await reader.read(1)
                if not byte:
                    return None
                encoded = byte[0]
                value += (encoded & 127) * multiplier
                if (encoded & 128) == 0:
                    return value
                multiplier *= 128
            except Exception:
                return None

        return None

    async def _handle_connect(self, payload: bytes, writer: asyncio.StreamWriter) -> bool:
        """Handle MQTT CONNECT packet.

        Returns True if authentication successful.
        """
        try:
            # Parse CONNECT packet
            # Skip protocol name length and name
            idx = 0
            proto_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2 + proto_len

            # Skip protocol level and connect flags
            # connect_flags = payload[idx + 1]
            idx += 2

            # Skip keepalive
            idx += 2

            # Read client ID
            client_id_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            # client_id = payload[idx : idx + client_id_len].decode("utf-8")
            idx += client_id_len

            # Read username
            username_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            username = payload[idx : idx + username_len].decode("utf-8")
            idx += username_len

            # Read password
            password_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            password = payload[idx : idx + password_len].decode("utf-8")

            # Authenticate
            if username == "bblp" and password == self.access_code:
                # Send CONNACK with success
                writer.write(bytes([0x20, 0x02, 0x00, 0x00]))
                await writer.drain()
                logger.info("MQTT client authenticated successfully")

                # Send immediate status report after auth - slicer expects this
                await self._send_status_report(writer)
                return True
            else:
                # Send CONNACK with auth failure
                writer.write(bytes([0x20, 0x02, 0x00, 0x05]))  # Not authorized
                await writer.drain()
                logger.warning(f"MQTT auth failed for user '{username}'")
                return False

        except Exception as e:
            logger.debug(f"MQTT CONNECT parse error: {e}")
            # Send CONNACK with error
            writer.write(bytes([0x20, 0x02, 0x00, 0x02]))  # Protocol error
            await writer.drain()
            return False

    async def _handle_subscribe(self, payload: bytes, writer: asyncio.StreamWriter) -> None:
        """Handle MQTT SUBSCRIBE packet."""
        try:
            # Parse packet ID
            packet_id = (payload[0] << 8) | payload[1]

            # Parse topic filters (just acknowledge them)
            idx = 2
            granted_qos = []
            while idx < len(payload):
                topic_len = (payload[idx] << 8) | payload[idx + 1]
                idx += 2
                topic = payload[idx : idx + topic_len].decode("utf-8")
                idx += topic_len
                requested_qos = payload[idx]
                idx += 1

                logger.info(f"MQTT subscribe: {topic} QoS={requested_qos}")
                granted_qos.append(min(requested_qos, 1))  # Grant up to QoS 1

            # Send SUBACK
            suback = bytes([0x90, 2 + len(granted_qos), packet_id >> 8, packet_id & 0xFF])
            suback += bytes(granted_qos)
            writer.write(suback)
            await writer.drain()

            # Send initial status report after subscribe
            await self._send_status_report(writer)

        except Exception as e:
            logger.debug(f"MQTT SUBSCRIBE error: {e}")

    async def _send_status_report(self, writer: asyncio.StreamWriter) -> None:
        """Send a status report to the slicer after connection."""
        try:
            # Build status message matching Bambu printer format
            self._sequence_id += 1
            status = {
                "print": {
                    "sequence_id": str(self._sequence_id),
                    "command": "push_status",
                    "msg": 0,
                    "gcode_state": "IDLE",
                    "gcode_file": "",
                    "gcode_file_prepare_percent": "0",
                    "subtask_name": "",
                    "mc_print_stage": "",
                    "mc_percent": 0,
                    "mc_remaining_time": 0,
                    "wifi_signal": "-44dBm",
                    "print_error": 0,
                    "print_type": "",
                    "bed_temper": 25.0,
                    "bed_target_temper": 0.0,
                    "nozzle_temper": 25.0,
                    "nozzle_target_temper": 0.0,
                    "chamber_temper": 25.0,
                    "cooling_fan_speed": "0",
                    "big_fan1_speed": "0",
                    "big_fan2_speed": "0",
                    "heatbreak_fan_speed": "0",
                    "spd_lvl": 1,
                    "spd_mag": 100,
                    "stg": [],
                    "stg_cur": 0,
                    "layer_num": 0,
                    "total_layer_num": 0,
                    "home_flag": 256,  # Bit 8 = SD card present (HAS_SDCARD_NORMAL)
                    "hw_switch_state": 0,
                    "online": {"ahb": False, "rfid": False, "version": 7},
                    "ams_status": 0,
                    "sdcard": True,
                    "storage": {"free": 1000000000, "total": 32000000000},
                    "upgrade_state": {
                        "sequence_id": 0,
                        "progress": "",
                        "status": "",
                        "consistency_request": False,
                        "dis_state": 0,
                        "err_code": 0,
                        "force_upgrade": False,
                        "message": "",
                        "module": "",
                        "new_version_state": 2,
                        "new_ver_list": [],
                        "ota_new_version_number": "",
                        "ahb_new_version_number": "",
                    },
                    "ipcam": {
                        "ipcam_dev": "1",
                        "ipcam_record": "enable",
                        "timelapse": "disable",
                        "resolution": "1080p",
                        "mode_bits": 0,
                    },
                    "xcam": {
                        "allow_skip_parts": False,
                        "buildplate_marker_detector": True,
                        "first_layer_inspector": True,
                        "halt_print_sensitivity": "medium",
                        "print_halt": True,
                        "printing_monitor": True,
                        "spaghetti_detector": True,
                    },
                    "lights_report": [{"node": "chamber_light", "mode": "on"}],
                    "nozzle_diameter": "0.4",
                    "nozzle_type": "hardened_steel",
                }
            }

            topic = f"device/{self.serial}/report"
            message = json.dumps(status)

            # Build MQTT PUBLISH packet
            topic_bytes = topic.encode("utf-8")
            message_bytes = message.encode("utf-8")

            # Calculate remaining length
            remaining = 2 + len(topic_bytes) + len(message_bytes)

            # Build packet
            packet = bytes([0x30])  # PUBLISH, QoS 0

            # Encode remaining length
            while remaining > 0:
                byte = remaining % 128
                remaining //= 128
                if remaining > 0:
                    byte |= 0x80
                packet += bytes([byte])

            # Topic length and topic
            packet += bytes([len(topic_bytes) >> 8, len(topic_bytes) & 0xFF])
            packet += topic_bytes

            # Message payload
            packet += message_bytes

            writer.write(packet)
            await writer.drain()

            logger.info(f"Sent initial status report on {topic}")

        except Exception as e:
            logger.error(f"Failed to send status report: {e}")

    async def _send_version_response(self, writer: asyncio.StreamWriter, sequence_id: str) -> None:
        """Send version info response to the slicer."""
        try:
            # Build version response matching OrcaSlicer expectations
            # Required fields per module: name, product_name, sw_ver, sw_new_ver, sn, hw_ver, flag
            version_info = {
                "info": {
                    "command": "get_version",
                    "sequence_id": sequence_id,
                    "module": [
                        {
                            "name": "ota",
                            "product_name": "X1 Carbon",
                            "sw_ver": "01.07.00.00",
                            "sw_new_ver": "",
                            "hw_ver": "OTA",
                            "sn": self.serial,
                            "flag": 0,
                        },
                        {
                            "name": "esp32",
                            "product_name": "X1 Carbon",
                            "sw_ver": "01.07.22.25",
                            "sw_new_ver": "",
                            "hw_ver": "AP05",
                            "sn": self.serial,
                            "flag": 0,
                        },
                        {
                            "name": "rv1126",
                            "product_name": "X1 Carbon",
                            "sw_ver": "00.00.27.38",
                            "sw_new_ver": "",
                            "hw_ver": "AP05",
                            "sn": self.serial,
                            "flag": 0,
                        },
                        {
                            "name": "th",
                            "product_name": "X1 Carbon",
                            "sw_ver": "00.00.04.00",
                            "sw_new_ver": "",
                            "hw_ver": "TH07",
                            "sn": self.serial,
                            "flag": 0,
                        },
                        {
                            "name": "mc",
                            "product_name": "X1 Carbon",
                            "sw_ver": "00.00.10.00",
                            "sw_new_ver": "",
                            "hw_ver": "MC07",
                            "sn": self.serial,
                            "flag": 0,
                        },
                    ],
                }
            }

            topic = f"device/{self.serial}/report"
            message = json.dumps(version_info)

            # Build MQTT PUBLISH packet
            topic_bytes = topic.encode("utf-8")
            message_bytes = message.encode("utf-8")

            # Calculate remaining length
            remaining = 2 + len(topic_bytes) + len(message_bytes)

            # Build packet
            packet = bytes([0x30])  # PUBLISH, QoS 0

            # Encode remaining length
            while remaining > 0:
                byte = remaining % 128
                remaining //= 128
                if remaining > 0:
                    byte |= 0x80
                packet += bytes([byte])

            # Topic length and topic
            packet += bytes([len(topic_bytes) >> 8, len(topic_bytes) & 0xFF])
            packet += topic_bytes

            # Message payload
            packet += message_bytes

            writer.write(packet)
            await writer.drain()

            logger.info(f"Sent version response on {topic}")

        except Exception as e:
            logger.error(f"Failed to send version response: {e}")

    async def _handle_publish(self, header: int, payload: bytes, writer: asyncio.StreamWriter) -> None:
        """Handle MQTT PUBLISH packet."""
        try:
            # Parse topic
            idx = 0
            topic_len = (payload[idx] << 8) | payload[idx + 1]
            idx += 2
            topic = payload[idx : idx + topic_len].decode("utf-8")
            idx += topic_len

            # Check for packet ID (QoS > 0)
            qos = (header & 0x06) >> 1
            if qos > 0:
                # packet_id = (payload[idx] << 8) | payload[idx + 1]
                idx += 2

            # Parse message
            message = payload[idx:].decode("utf-8")

            logger.info(f"MQTT publish to {topic}: {message[:100]}...")

            # Handle commands on device request topic
            if f"device/{self.serial}/request" in topic:
                try:
                    data = json.loads(message)

                    # Handle pushing command (status request)
                    if "pushing" in data:
                        pushing_data = data["pushing"]
                        command = pushing_data.get("command", "")
                        logger.info(f"MQTT pushing command: {command}")

                        if command == "pushall":
                            # Slicer is requesting full status - send response
                            logger.info("Sending status report in response to pushall")
                            await self._send_status_report(writer)
                        elif command == "start":
                            # Slicer wants periodic status updates - send one now
                            logger.info("Starting status push stream")
                            await self._send_status_report(writer)

                    # Handle info commands (get_version, etc.)
                    if "info" in data:
                        info_data = data["info"]
                        command = info_data.get("command", "")
                        sequence_id = info_data.get("sequence_id", "0")
                        logger.info(f"MQTT info command: {command}")

                        if command == "get_version":
                            await self._send_version_response(writer, sequence_id)

                    # Handle print commands
                    if "print" in data:
                        print_data = data["print"]
                        command = print_data.get("command", "")
                        filename = print_data.get("subtask_name", "")

                        logger.info(f"MQTT print command: {command} for {filename}")

                        if self.on_print_command and command == "project_file":
                            await self._notify_print_command(filename, print_data)

                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.debug(f"MQTT PUBLISH error: {e}")

    async def _notify_print_command(self, filename: str, data: dict) -> None:
        """Notify callback of print command."""
        if self.on_print_command:
            try:
                result = self.on_print_command(filename, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Print command callback error: {e}")
