"""Bind/detect server for virtual printer discovery (port 3000).

Bambu slicers (BambuStudio, OrcaSlicer) connect to port 3000 on a printer
to perform the "bind with access code" handshake before using MQTT/FTP.

Protocol:
  - Framing: 0xA5A5 + uint16_le(total_msg_size) + JSON payload + 0xA7A7
  - Slicer sends: {"login":{"command":"detect","sequence_id":"20000"}}
  - Printer replies: {"login":{"bind":"free","command":"detect","connect":"lan",
      "dev_cap":1,"id":"<serial>","model":"<model>","name":"<name>",
      "sequence_id":<int>,"version":"<firmware>"}}
  - Connection closes after one exchange.
"""

import asyncio
import json
import logging
import struct

logger = logging.getLogger(__name__)

BIND_PORT = 3000
FRAME_HEADER = b"\xa5\xa5"
FRAME_TRAILER = b"\xa7\xa7"
HEADER_SIZE = 4  # 2 bytes magic + 2 bytes length
TRAILER_SIZE = 2


class BindServer:
    """Responds to slicer bind/detect requests on port 3000.

    In server mode, Bambuddy IS the printer — it responds with its own
    identity so the slicer can discover and bind to it.
    """

    def __init__(
        self,
        serial: str,
        model: str,
        name: str,
        version: str = "01.00.00.00",
        bind_address: str = "0.0.0.0",  # nosec B104
    ):
        self.serial = serial
        self.model = model
        self.name = name
        self.version = version
        self.bind_address = bind_address

        self._server: asyncio.Server | None = None
        self._running = False

    async def start(self) -> None:
        """Start the bind server on port 3000."""
        if self._running:
            return

        logger.info("Starting bind server on port %s (serial=%s, model=%s)", BIND_PORT, self.serial, self.model)

        try:
            self._running = True
            self._server = await asyncio.start_server(
                self._handle_client,
                self.bind_address,
                BIND_PORT,
            )

            logger.info("Bind server listening on port %s", BIND_PORT)

            async with self._server:
                await self._server.serve_forever()

        except OSError as e:
            if e.errno == 98:
                logger.error("Bind server port %s is already in use", BIND_PORT)
            elif e.errno == 13:
                logger.error("Bind server: cannot bind to port %s (permission denied)", BIND_PORT)
            else:
                logger.error("Bind server error: %s", e)
        except asyncio.CancelledError:
            logger.debug("Bind server task cancelled")
        except Exception as e:
            logger.error("Bind server error: %s", e)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the bind server."""
        logger.info("Stopping bind server")
        self._running = False

        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except OSError as e:
                logger.debug("Error closing bind server: %s", e)
            self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single bind/detect request from a slicer."""
        peername = writer.get_extra_info("peername")
        client_id = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        logger.info("Bind server: client connected from %s", client_id)

        try:
            # Read the framed message (timeout after 10s)
            data = await asyncio.wait_for(reader.read(4096), timeout=10.0)
            if not data:
                return

            # Parse the request
            request = self._parse_frame(data)
            if request is None:
                logger.warning("Bind server: invalid frame from %s", client_id)
                return

            logger.info("Bind server: received from %s: %s", client_id, request)

            # Check if this is a detect command
            login = request.get("login", {})
            if not isinstance(login, dict) or login.get("command") != "detect":
                logger.warning("Bind server: unexpected command from %s: %s", client_id, request)
                return

            # Build response
            response = {
                "login": {
                    "bind": "free",
                    "command": "detect",
                    "connect": "lan",
                    "dev_cap": 1,
                    "id": self.serial,
                    "model": self.model,
                    "name": self.name,
                    "sequence_id": 3021,
                    "version": self.version,
                }
            }

            frame = self._build_frame(response)
            writer.write(frame)
            await writer.drain()

            logger.info("Bind server: sent detect response to %s (serial=%s)", client_id, self.serial)

        except TimeoutError:
            logger.debug("Bind server: timeout waiting for data from %s", client_id)
        except Exception as e:
            logger.error("Bind server: error handling %s: %s", client_id, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass
            logger.debug("Bind server: client %s disconnected", client_id)

    def _parse_frame(self, data: bytes) -> dict | None:
        """Parse a framed message: 0xA5A5 + len(u16le) + JSON + 0xA7A7."""
        if len(data) < HEADER_SIZE + TRAILER_SIZE:
            return None

        if data[:2] != FRAME_HEADER:
            return None

        if data[-2:] != FRAME_TRAILER:
            return None

        # Length field is total message size (header + json + trailer)
        total_len = struct.unpack_from("<H", data, 2)[0]
        if total_len != len(data):
            logger.debug("Bind frame length mismatch: header says %d, got %d", total_len, len(data))

        # JSON payload is between header and trailer
        json_bytes = data[HEADER_SIZE:-TRAILER_SIZE]
        try:
            return json.loads(json_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Bind server: failed to parse JSON: %s", e)
            return None

    def _build_frame(self, payload: dict) -> bytes:
        """Build a framed message: 0xA5A5 + len(u16le) + JSON + 0xA7A7."""
        json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        total_len = HEADER_SIZE + len(json_bytes) + TRAILER_SIZE
        header = FRAME_HEADER + struct.pack("<H", total_len)
        return header + json_bytes + FRAME_TRAILER
