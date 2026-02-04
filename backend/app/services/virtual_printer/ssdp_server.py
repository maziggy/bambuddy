"""SSDP discovery responder for virtual printer.

Responds to M-SEARCH requests from slicers and sends periodic NOTIFY
announcements so the virtual printer appears as a discoverable Bambu printer.

Also provides SSDP proxy functionality for proxy mode, where Bambuddy sits
between two networks and re-broadcasts printer SSDP from LAN A to LAN B.
"""

import asyncio
import logging
import re
import socket
import struct

logger = logging.getLogger(__name__)

# SSDP addresses - Bambu uses port 2021
# Real Bambu printers broadcast to 255.255.255.255, not multicast to 239.255.255.250
SSDP_MULTICAST_ADDR = "239.255.255.250"
SSDP_BROADCAST_ADDR = "255.255.255.255"
SSDP_PORT = 2021

# Bambu service target
BAMBU_SEARCH_TARGET = "urn:bambulab-com:device:3dprinter:1"


class VirtualPrinterSSDPServer:
    """SSDP server that responds to discovery requests as a virtual Bambu printer."""

    def __init__(
        self,
        name: str = "Bambuddy",
        serial: str = "00M09A391800001",  # X1C serial format for compatibility
        model: str = "BL-P001",  # X1C model code for best compatibility
    ):
        """Initialize the SSDP server.

        Args:
            name: Display name shown in slicer discovery
            serial: Unique serial number for this virtual printer (must match cert CN)
            model: Model code (BL-P001=X1C, C11=P1S, O1D=H2D)
        """
        self.name = name
        self.serial = serial
        self.model = model
        self._running = False
        self._socket: socket.socket | None = None
        self._local_ip: str | None = None

    def _get_local_ip(self) -> str:
        """Get the local IP address to advertise."""
        if self._local_ip:
            return self._local_ip

        # Try to determine local IP by connecting to a public address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            self._local_ip = ip
            return ip
        except Exception:
            return "127.0.0.1"

    def _build_notify_message(self) -> bytes:
        """Build SSDP NOTIFY message for periodic announcements.

        Format matches real Bambu printer SSDP broadcasts observed on the network.
        Real printers use Host: 239.255.255.250:1990 (port 1990 in header).
        """
        ip = self._get_local_ip()
        # Match exact format of real Bambu printers (captured via tcpdump)
        # Key: DevBind.bambu.com: free - tells slicer printer is NOT cloud-bound
        message = (
            "NOTIFY * HTTP/1.1\r\n"
            f"Host: {SSDP_MULTICAST_ADDR}:1990\r\n"
            "Server: UPnP/1.0\r\n"
            f"Location: {ip}\r\n"
            f"NT: {BAMBU_SEARCH_TARGET}\r\n"
            "NTS: ssdp:alive\r\n"
            f"USN: {self.serial}\r\n"
            "Cache-Control: max-age=1800\r\n"
            f"DevModel.bambu.com: {self.model}\r\n"
            f"DevName.bambu.com: {self.name}\r\n"
            "DevSignal.bambu.com: -44\r\n"
            "DevConnect.bambu.com: lan\r\n"
            "DevBind.bambu.com: free\r\n"
            "Devseclink.bambu.com: secure\r\n"
            "DevInf.bambu.com: eth0\r\n"
            "DevVersion.bambu.com: 01.07.00.00\r\n"
            "DevCap.bambu.com: 1\r\n"
            "\r\n"
        )
        return message.encode()

    def _build_response_message(self) -> bytes:
        """Build SSDP response message for M-SEARCH requests.

        Format matches real Bambu printer SSDP responses.
        """
        ip = self._get_local_ip()
        # Match format of real Bambu printers
        # Key: DevBind.bambu.com: free - tells slicer printer is NOT cloud-bound
        message = (
            "HTTP/1.1 200 OK\r\n"
            "Server: UPnP/1.0\r\n"
            f"Location: {ip}\r\n"
            f"ST: {BAMBU_SEARCH_TARGET}\r\n"
            f"USN: {self.serial}\r\n"
            "Cache-Control: max-age=1800\r\n"
            f"DevModel.bambu.com: {self.model}\r\n"
            f"DevName.bambu.com: {self.name}\r\n"
            "DevSignal.bambu.com: -44\r\n"
            "DevConnect.bambu.com: lan\r\n"
            "DevBind.bambu.com: free\r\n"
            "Devseclink.bambu.com: secure\r\n"
            "DevInf.bambu.com: eth0\r\n"
            "DevVersion.bambu.com: 01.07.00.00\r\n"
            "DevCap.bambu.com: 1\r\n"
            "\r\n"
        )
        return message.encode()

    async def start(self) -> None:
        """Start the SSDP server."""
        if self._running:
            return

        logger.info(f"Starting virtual printer SSDP server: {self.name} ({self.serial})")
        self._running = True

        try:
            # Create UDP socket
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Try to set SO_REUSEPORT if available
            try:
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass

            # Set non-blocking mode
            self._socket.setblocking(False)

            # Bind to SSDP port
            self._socket.bind(("", SSDP_PORT))

            # Join multicast group
            mreq = struct.pack("4sl", socket.inet_aton(SSDP_MULTICAST_ADDR), socket.INADDR_ANY)
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            # Enable broadcast
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Set multicast TTL
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

            local_ip = self._get_local_ip()
            logger.info(f"SSDP server listening on port {SSDP_PORT}, advertising IP: {local_ip}")
            logger.info(f"Virtual printer: {self.name} ({self.serial}) model={self.model}")

            # Send initial NOTIFY
            await self._send_notify()
            logger.info("Sent initial SSDP NOTIFY announcement")

            # Run receive and announce loops
            last_notify = asyncio.get_event_loop().time()
            notify_interval = 30.0  # Send NOTIFY every 30 seconds

            while self._running:
                # Try to receive M-SEARCH requests
                try:
                    data, addr = self._socket.recvfrom(4096)
                    message = data.decode("utf-8", errors="ignore")
                    await self._handle_message(message, addr)
                except BlockingIOError:
                    pass
                except Exception as e:
                    if self._running:
                        logger.debug(f"SSDP receive error: {e}")

                # Send periodic NOTIFY
                now = asyncio.get_event_loop().time()
                if now - last_notify >= notify_interval:
                    await self._send_notify()
                    last_notify = now

                await asyncio.sleep(0.1)

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.warning(f"SSDP port {SSDP_PORT} in use - real printers may be running")
            else:
                logger.error(f"SSDP server error: {e}")
        except asyncio.CancelledError:
            logger.debug("SSDP server cancelled")
        except Exception as e:
            logger.error(f"SSDP server error: {e}")
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the SSDP server."""
        logger.info("Stopping SSDP server")
        self._running = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._socket:
            try:
                # Send byebye message
                await self._send_byebye()
            except Exception:
                pass

            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    async def _send_notify(self) -> None:
        """Send SSDP NOTIFY message via broadcast (like real Bambu printers)."""
        if not self._socket:
            return

        try:
            msg = self._build_notify_message()
            # Real Bambu printers broadcast to 255.255.255.255, not multicast
            self._socket.sendto(msg, (SSDP_BROADCAST_ADDR, SSDP_PORT))
            logger.debug(f"Sent SSDP NOTIFY for {self.name}")
        except Exception as e:
            logger.debug(f"Failed to send NOTIFY: {e}")

    async def _send_byebye(self) -> None:
        """Send SSDP byebye message when shutting down."""
        if not self._socket:
            return

        message = (
            "NOTIFY * HTTP/1.1\r\n"
            f"Host: {SSDP_MULTICAST_ADDR}:1990\r\n"
            f"NT: {BAMBU_SEARCH_TARGET}\r\n"
            "NTS: ssdp:byebye\r\n"
            f"USN: {self.serial}\r\n"
            "\r\n"
        )

        try:
            self._socket.sendto(message.encode(), (SSDP_BROADCAST_ADDR, SSDP_PORT))
            logger.debug("Sent SSDP byebye")
        except Exception:
            pass

    async def _handle_message(self, message: str, addr: tuple[str, int]) -> None:
        """Handle incoming SSDP message.

        Args:
            message: The SSDP message content
            addr: Tuple of (ip_address, port) of sender
        """
        # Check if this is an M-SEARCH request for Bambu printers
        if "M-SEARCH" not in message:
            return

        # Check search target
        if BAMBU_SEARCH_TARGET not in message and "ssdp:all" not in message.lower():
            return

        logger.debug(f"Received M-SEARCH from {addr[0]}")

        # Send response
        if self._socket:
            try:
                response = self._build_response_message()
                self._socket.sendto(response, addr)
                logger.info(f"Sent SSDP response to {addr[0]} for virtual printer '{self.name}'")
            except Exception as e:
                logger.debug(f"Failed to send SSDP response: {e}")


class SSDPProxy:
    """SSDP proxy that re-broadcasts printer discovery from one network to another.

    Listens for SSDP broadcasts from a real printer on the local interface (LAN A),
    then re-broadcasts them on the remote interface (LAN B) with the Location
    header changed to point to Bambuddy's IP on LAN B.

    This allows Bambu Studio on LAN B to discover the printer via Bambuddy.
    """

    def __init__(
        self,
        local_interface_ip: str,
        remote_interface_ip: str,
        target_printer_ip: str,
    ):
        """Initialize the SSDP proxy.

        Args:
            local_interface_ip: IP of interface on printer's network (LAN A)
            remote_interface_ip: IP of interface on slicer's network (LAN B)
            target_printer_ip: IP of the real printer to proxy SSDP for
        """
        self.local_interface_ip = local_interface_ip
        self.remote_interface_ip = remote_interface_ip
        self.target_printer_ip = target_printer_ip
        self._running = False
        self._local_socket: socket.socket | None = None
        self._remote_socket: socket.socket | None = None
        self._last_printer_ssdp: bytes | None = None
        self._printer_info: dict[str, str] = {}

    def _parse_ssdp_message(self, data: bytes) -> dict[str, str]:
        """Parse SSDP message into header dict."""
        headers = {}
        try:
            text = data.decode("utf-8", errors="ignore")
            for line in text.split("\r\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
        except Exception:
            pass
        return headers

    def _rewrite_ssdp_location(self, data: bytes) -> bytes:
        """Rewrite SSDP message with Bambuddy's remote IP as Location."""
        try:
            text = data.decode("utf-8", errors="ignore")
            original = text
            # Replace Location header with our remote interface IP
            text = re.sub(
                r"(Location:\s*)[\d.]+",
                f"\\g<1>{self.remote_interface_ip}",
                text,
                flags=re.IGNORECASE,
            )
            if text != original:
                logger.debug(f"Rewrote SSDP Location to {self.remote_interface_ip}")
                logger.debug(f"Rewritten SSDP packet:\n{text}")
            else:
                logger.warning(f"SSDP Location rewrite had no effect. Packet:\n{original}")
            return text.encode("utf-8")
        except Exception as e:
            logger.error(f"Failed to rewrite SSDP: {e}")
            return data

    async def start(self) -> None:
        """Start the SSDP proxy."""
        if self._running:
            return

        logger.info(
            f"Starting SSDP proxy: listening on {self.local_interface_ip} (LAN A), "
            f"broadcasting on {self.remote_interface_ip} (LAN B), "
            f"proxying printer {self.target_printer_ip}"
        )
        self._running = True

        try:
            # Create socket for listening on LAN A (printer network)
            # Bind to 0.0.0.0 to receive broadcast packets (255.255.255.255)
            # We filter by source IP in the handler
            self._local_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            self._local_socket.setblocking(False)
            # Bind to all interfaces to receive broadcasts
            self._local_socket.bind(("", SSDP_PORT))

            # Join multicast group on local interface (for multicast SSDP if used)
            mreq = struct.pack(
                "4s4s",
                socket.inet_aton(SSDP_MULTICAST_ADDR),
                socket.inet_aton(self.local_interface_ip),
            )
            self._local_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self._local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Create socket for broadcasting on LAN B (slicer network)
            self._remote_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._remote_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._remote_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            self._remote_socket.setblocking(False)
            # Bind to remote interface
            self._remote_socket.bind((self.remote_interface_ip, 0))
            self._remote_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            logger.info(f"SSDP proxy listening on 0.0.0.0:{SSDP_PORT} (filtering for printer {self.target_printer_ip})")
            logger.info(f"SSDP proxy will broadcast on {self.remote_interface_ip}")

            # Main loop
            last_broadcast = 0.0
            broadcast_interval = 30.0  # Re-broadcast every 30 seconds

            while self._running:
                # Listen for SSDP from printer on LAN A
                try:
                    data, addr = self._local_socket.recvfrom(4096)
                    await self._handle_local_packet(data, addr)
                except BlockingIOError:
                    pass
                except Exception as e:
                    if self._running:
                        logger.debug(f"SSDP proxy receive error: {e}")

                # Listen for M-SEARCH from slicer on LAN B (via remote socket would need separate bind)
                # For now, we periodically re-broadcast cached printer SSDP
                now = asyncio.get_event_loop().time()
                if self._last_printer_ssdp and now - last_broadcast >= broadcast_interval:
                    await self._broadcast_to_remote()
                    last_broadcast = now

                await asyncio.sleep(0.1)

        except OSError as e:
            logger.error(f"SSDP proxy error: {e}")
        except asyncio.CancelledError:
            logger.debug("SSDP proxy cancelled")
        except Exception as e:
            logger.error(f"SSDP proxy error: {e}")
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the SSDP proxy."""
        logger.info("Stopping SSDP proxy")
        self._running = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        for sock in [self._local_socket, self._remote_socket]:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._local_socket = None
        self._remote_socket = None

    async def _handle_local_packet(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle SSDP packet received on local interface (LAN A)."""
        sender_ip = addr[0]

        # Only process packets from the target printer
        if sender_ip != self.target_printer_ip:
            return

        # Check if it's a NOTIFY message
        if b"NOTIFY" not in data and b"HTTP/1.1 200" not in data:
            return

        # Check if it's a Bambu printer SSDP
        if b"bambulab-com:device:3dprinter" not in data:
            return

        # Parse and store printer info
        headers = self._parse_ssdp_message(data)
        if headers:
            self._printer_info = headers
            logger.debug(f"Received SSDP from printer {sender_ip}: {headers.get('devname.bambu.com', 'unknown')}")

        # Store and immediately broadcast
        self._last_printer_ssdp = data
        await self._broadcast_to_remote()

    async def _broadcast_to_remote(self) -> None:
        """Broadcast cached printer SSDP on remote interface (LAN B)."""
        if not self._remote_socket or not self._last_printer_ssdp:
            return

        try:
            # Rewrite Location to point to Bambuddy's remote interface
            rewritten = self._rewrite_ssdp_location(self._last_printer_ssdp)

            # Calculate broadcast address for remote network
            # Use 255.255.255.255 for simplicity (works across subnets)
            self._remote_socket.sendto(rewritten, (SSDP_BROADCAST_ADDR, SSDP_PORT))

            printer_name = self._printer_info.get("devname.bambu.com", "unknown")
            logger.debug(f"Broadcast SSDP for '{printer_name}' on LAN B ({self.remote_interface_ip})")
        except Exception as e:
            logger.debug(f"Failed to broadcast SSDP on remote: {e}")
