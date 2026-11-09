"""The slicer's camera view through a virtual printer, signed in for it (#1430).

A slicer bound to a virtual printer opens the camera on the virtual printer's
address. A plain pass-through to the real printer only worked with the
printer's own access code: the printer checks the code itself, so a slicer
signed in with a personal access code was refused there, and the slicer then
asked for the code again on every action.

This bridge ends the slicer's TLS with the virtual printer's certificate (the
one the slicer already trusts for MQTT and FTP), opens its own TLS connection
to the printer, and passes the printer's own code on when the slicer's code is
one it accepts:

* RTSPS (port 322, X1/X2/H2/P2S): the printer asks for Digest auth, so the
  code never crosses the wire, only a hash of it. Each ``Authorization``
  header is checked against the accepted codes and, on a match, its hash is
  computed anew with the printer's code. Nonce and URI stay the slicer's.
* Chamber image (port 6000, A1/P1): the code is in plain text in the first 80
  bytes; it is checked and replaced by the printer's.

Codes it accepts (``codes``): the virtual printer's, the printer's own, and
the personal codes of users allowed to watch this printer now (looked up
when a sign-in arrives, again after ``CODES_MAX_AGE``). A sign-in the bridge
does not accept is never passed on: it counts as a failed sign-in and ends
the connection. Otherwise the camera would be a way to guess codes, the
printer's own among them, with no limit.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import ssl
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from backend.app.services.virtual_printer.ftp_server import AuthRateLimiter

logger = logging.getLogger(__name__)

RTSP = "rtsp"
CHAMBER = "chamber"

# Chamber image sign-in: magic, command, padding, user (32), code (32).
_CHAMBER_AUTH = struct.Struct("<II8s32s32s")
_CHAMBER_MAGIC = 0x40
_CHAMBER_COMMAND = 0x3000

_DIGEST_PARAM = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')
_DIGEST_RESPONSE = re.compile(r'(response\s*=\s*)(?:"[^"]*"|[^\s,]+)', re.IGNORECASE)
_MAX_RTSP_BODY = 1 << 20


@dataclass(frozen=True)
class AcceptedCode:
    """A code the camera accepts, and the user whose personal code it is (None for a shared one)."""

    code: str
    user_id: int | None = None


def parse_digest(value: str) -> dict[str, str] | None:
    """The parameters of a ``Digest ...`` Authorization value, or None for another scheme."""
    scheme, _, rest = value.strip().partition(" ")
    if scheme.lower() != "digest":
        return None
    return {
        m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3) for m in _DIGEST_PARAM.finditer(rest)
    }


def digest_response(params: dict[str, str], method: str, password: str) -> str | None:
    """The Digest ``response`` (RFC 2617, MD5) for ``params`` signed with ``password``.

    None when a field it needs is missing or the algorithm is not MD5.
    """
    if params.get("algorithm", "MD5").upper() != "MD5":
        return None
    try:
        username, realm, nonce, uri = params["username"], params["realm"], params["nonce"], params["uri"]
    except KeyError:
        return None

    def md5(text: str) -> str:
        return hashlib.md5(text.encode("latin-1"), usedforsecurity=False).hexdigest()

    ha1 = md5(f"{username}:{realm}:{password}")
    ha2 = md5(f"{method}:{uri}")
    qop = params.get("qop")
    if qop:
        nc, cnonce = params.get("nc"), params.get("cnonce")
        if nc is None or cnonce is None:
            return None
        return md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5(f"{ha1}:{nonce}:{ha2}")


def match_digest(params: dict[str, str], method: str, accepted: list[AcceptedCode]) -> AcceptedCode | None:
    """The accepted code the slicer's Digest response was made with, if any."""
    given = params.get("response", "").lower()
    found = None
    for candidate in accepted:
        expected = digest_response(params, method, candidate.code)
        # Every candidate is tried, so the time taken does not tell which matched.
        if expected is not None and hmac.compare_digest(expected, given) and found is None:
            found = candidate
    return found


def rewrite_rtsp_auth(
    head: bytes, accepted: list[AcceptedCode], printer_code: str
) -> tuple[bytes, AcceptedCode | None, bool]:
    """Sign an RTSP request's Digest header with the printer's code.

    Returns ``(head, matched, had_auth)``: the request head (rewritten when
    the slicer's code is accepted, else unchanged), the code it matched, and
    whether the request tries to sign in at all.

    Fail-closed: ``had_auth`` is True for any request that mentions
    "authorization" anywhere, and it is only re-signed when that is one
    ``Authorization: Digest`` header with an accepted code. The printer's
    server finds a Digest header wherever it is in the request (in another
    header's value, or after a Basic one), so a request the bridge does not
    re-sign must never reach it: its answer would tell whether a guessed code
    is right, without the guess being counted.
    """
    if b"authorization" not in head.lower():
        return head, None, False
    if head.lower().count(b"authorization") != 1:
        return head, None, True
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    method = lines[0].split(" ", 1)[0] if lines else ""
    for i, line in enumerate(lines[1:], start=1):
        name, sep, value = line.partition(":")
        if not sep or name.strip().lower() != "authorization":
            continue
        params = parse_digest(value)
        if params is None:
            return head, None, True
        matched = match_digest(params, method, accepted)
        if matched is None:
            return head, None, True
        new = digest_response(params, method, printer_code)
        if new is None:
            return head, None, True
        signed = _DIGEST_RESPONSE.sub(lambda m, new=new: m.group(1) + '"' + new + '"', value, count=1)
        lines[i] = f"{name}:{signed}"
        return "\r\n".join(lines).encode("latin-1"), matched, True
    return head, None, True


def rewrite_chamber_auth(
    payload: bytes, accepted: list[AcceptedCode], printer_code: str
) -> tuple[bytes, AcceptedCode | None]:
    """The chamber image sign-in with the printer's code, when the slicer's is accepted."""
    if len(payload) != _CHAMBER_AUTH.size:
        return payload, None
    magic, command, padding, user, code = _CHAMBER_AUTH.unpack(payload)
    if magic != _CHAMBER_MAGIC or command != _CHAMBER_COMMAND:
        return payload, None
    given = code.rstrip(b"\x00")
    found = None
    for candidate in accepted:
        if hmac.compare_digest(candidate.code.encode(), given) and found is None:
            found = candidate
    if found is None:
        return payload, None
    return _CHAMBER_AUTH.pack(magic, command, padding, user, printer_code.encode().ljust(32, b"\x00")), found


def _server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # The suites real printers offer; see mqtt_server (#1610).
    context.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256")
    return context


def _printer_context() -> ssl.SSLContext:
    # The printer's certificate is self-signed by its maker; Bambuddy's own
    # camera connection does not check it either (services.camera).
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


FIRST_BYTES_TIMEOUT = 15.0
# How long a connection keeps the accepted codes it looked up: a user who
# loses camera:view, is disabled or renews their code is out within this.
CODES_MAX_AGE = 60.0


class _AcceptedCodes:
    """One connection's accepted codes: looked up when first needed, again once older than ``CODES_MAX_AGE``."""

    def __init__(self, lookup: Callable[[], Awaitable[list[AcceptedCode]]]):
        self._lookup = lookup
        self._codes: list[AcceptedCode] | None = None
        self._at = 0.0

    async def get(self) -> list[AcceptedCode]:
        now = time.monotonic()
        if self._codes is None or now - self._at > CODES_MAX_AGE:
            self._codes = await self._lookup()
            self._at = now
        return self._codes


class CameraBridge:
    """Camera port of a virtual printer with a target printer, signing the slicer in with the printer's code."""

    def __init__(
        self,
        *,
        name: str,
        protocol: str,
        listen_port: int,
        bind_address: str,
        target_host: str,
        target_port: int,
        cert_path: Path,
        key_path: Path,
        codes: Callable[[], Awaitable[list[AcceptedCode]]],
        printer_code: Callable[[], str | None],
        rate_limiter: AuthRateLimiter | None = None,
    ):
        self.name = name
        self.protocol = protocol
        self.listen_port = listen_port
        self.bind_address = bind_address
        self.target_host = target_host
        self.target_port = target_port
        self.cert_path = cert_path
        self.key_path = key_path
        self._codes = codes
        self._printer_code = printer_code
        self.rate_limiter = rate_limiter or AuthRateLimiter()
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Listen until stopped."""
        logger.info(
            "Starting %s camera bridge: %s:%s -> %s:%s",
            self.name,
            self.bind_address,
            self.listen_port,
            self.target_host,
            self.target_port,
        )
        try:
            self._server = await asyncio.start_server(
                self._handle,
                self.bind_address,
                self.listen_port,
                ssl=_server_context(self.cert_path, self.key_path),
            )
            # The server is serving already. Not ``serve_forever()`` nor
            # ``async with``: once cancelled, both wait for every connection to
            # close (Python 3.12+), before stop() below gets to end them, so a
            # slicer watching the camera kept the virtual printer from stopping.
            await asyncio.get_running_loop().create_future()
        except OSError as e:
            logger.error("%s camera bridge on port %s: %s", self.name, self.listen_port, e)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop listening and end every open camera connection."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        handlers = list(self._handlers)
        for task in handlers:
            task.cancel()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)
        # No wait_closed(): since Python 3.12 it waits for each connection's
        # TLS close, which a slicer that stopped reading holds up for 30 s.

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        if current is not None:
            self._handlers.add(current)
        peer = client_writer.get_extra_info("peername")
        source_ip = peer[0] if peer else "?"
        printer_writer = None
        try:
            printer_code = self._printer_code()
            if not printer_code:
                logger.warning("%s camera: the target printer's access code is not known", self.name)
                return
            if self.rate_limiter.is_limited(source_ip):
                logger.warning("%s camera from %s refused: too many failed attempts", self.name, source_ip)
                return
            # Nothing is looked up or opened for a connection that sends nothing.
            first = await asyncio.wait_for(
                client_reader.readexactly(_CHAMBER_AUTH.size if self.protocol == CHAMBER else 1),
                timeout=FIRST_BYTES_TIMEOUT,
            )
            codes = _AcceptedCodes(self._codes)
            if self.protocol == CHAMBER:
                first, matched = rewrite_chamber_auth(first, await codes.get(), printer_code)
                if matched is None:
                    self._refused(source_ip)
                    return
                self._signed_in(source_ip, matched, set())
            printer_reader, printer_writer = await asyncio.wait_for(
                asyncio.open_connection(self.target_host, self.target_port, ssl=_printer_context()),
                timeout=10.0,
            )
            logger.info("%s camera: %s connected", self.name, source_ip)
            if self.protocol == CHAMBER:
                printer_writer.write(first)
                await printer_writer.drain()
                pump = self._pipe(client_reader, printer_writer)
            else:
                pump = self._pump_rtsp(first, client_reader, printer_writer, source_ip, codes, printer_code)
            await asyncio.gather(pump, self._pipe(printer_reader, client_writer))
        except (ConnectionError, OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
            logger.debug("%s camera connection from %s ended: %s", self.name, source_ip, e)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 -- one camera connection must not take the bridge down
            logger.error("%s camera connection from %s failed: %s", self.name, source_ip, e)
        finally:
            if current is not None:
                self._handlers.discard(current)
            for writer in (client_writer, printer_writer):
                if writer is not None and not writer.is_closing():
                    try:
                        writer.close()
                    except (OSError, RuntimeError):
                        pass

    def _signed_in(self, source_ip: str, matched: AcceptedCode, logged: set[int | None]) -> None:
        if matched.user_id not in logged:
            logged.add(matched.user_id)
            if matched.user_id is not None:
                logger.info("%s camera: %s signed in as user %s", self.name, source_ip, matched.user_id)

    def _refused(self, source_ip: str) -> None:
        """Count a sign-in the camera doesn't accept; the caller then ends the connection."""
        self.rate_limiter.record_failure(source_ip)
        logger.warning("%s camera from %s: access code not accepted", self.name, source_ip)

    async def _pump_rtsp(
        self,
        first: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        source_ip: str,
        codes: _AcceptedCodes,
        printer_code: str,
    ) -> None:
        """Slicer -> printer: RTSP requests (Digest re-signed) and interleaved ``$`` frames as they are.

        A sign-in that is not re-signed ends the connection: it is never
        passed on (see ``rewrite_rtsp_auth``).
        """
        logged: set[int | None] = set()
        try:
            while True:
                if first == b"$":
                    header = await reader.readexactly(3)
                    frame = await reader.readexactly(int.from_bytes(header[1:3], "big"))
                    writer.write(first + header + frame)
                else:
                    head = first + await reader.readuntil(b"\r\n\r\n")
                    body = b""
                    match = re.search(rb"\r\ncontent-length\s*:\s*(\d+)", head, re.IGNORECASE)
                    if match:
                        length = int(match.group(1))
                        if length > _MAX_RTSP_BODY:
                            return
                        body = await reader.readexactly(length)
                    accepted = await codes.get() if b"authorization" in head.lower() else []
                    head, matched, had_auth = rewrite_rtsp_auth(head, accepted, printer_code)
                    if matched is not None:
                        self._signed_in(source_ip, matched, logged)
                    elif had_auth:
                        self._refused(source_ip)
                        return
                    writer.write(head + body)
                await writer.drain()
                first = await reader.readexactly(1)
        except asyncio.IncompleteReadError:
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError, RuntimeError):
            pass
        finally:
            if not writer.is_closing():
                try:
                    writer.close()
                except (OSError, RuntimeError):
                    pass
