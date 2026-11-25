"""The slicer's camera through a virtual printer signs in with the printer's code (#1430).

The printer checks the camera's access code itself, so a slicer bound with a
personal access code was refused there and kept asking for the code.
"""

import asyncio
import re
import ssl

import pytest

from backend.app.services.virtual_printer.camera_bridge import (
    CHAMBER,
    RTSP,
    AcceptedCode,
    CameraBridge,
    digest_response,
    parse_digest,
    rewrite_chamber_auth,
    rewrite_rtsp_auth,
)
from backend.app.services.virtual_printer.ftp_server import AuthRateLimiter

PRINTER_CODE = "12345678"
VP_CODE = "87654321"
PERSONAL = "11112222"
ACCEPTED = [AcceptedCode(VP_CODE), AcceptedCode(PERSONAL, user_id=7)]
REALM = "LIVE555 Streaming Media"
NONCE = "b0047e376b1831bbc8afa1461ba6c1ca"
URI = "rtsps://192.0.2.10:322/streaming/live/1"


def _signed(code: str, method: str = "DESCRIBE") -> dict[str, str]:
    params = {"username": "bblp", "realm": REALM, "nonce": NONCE, "uri": URI}
    params["response"] = digest_response(params, method, code)
    return params


def _request(code: str | None, method: str = "DESCRIBE", cseq: int = 2) -> bytes:
    lines = [f"{method} {URI} RTSP/1.0", f"CSeq: {cseq}"]
    if code is not None:
        p = _signed(code, method)
        lines.append(
            f'Authorization: Digest username="bblp", realm="{REALM}", nonce="{NONCE}", uri="{URI}", '
            f'response="{p["response"]}"'
        )
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _response_of(head: bytes) -> str:
    return re.search(rb'response="([0-9a-f]+)"', head).group(1).decode()


class TestDigest:
    def test_rfc2617_example(self):
        params = parse_digest(
            'Digest username="Mufasa", realm="testrealm@host.com", '
            'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", uri="/dir/index.html", qop=auth, '
            'nc=00000001, cnonce="0a4f113b", response="6629fae49393a05397450978507c4ef1", '
            'opaque="5ccc069c403ebaf9f0171e9517f40e41"'
        )
        assert digest_response(params, "GET", "Circle Of Life") == "6629fae49393a05397450978507c4ef1"

    def test_other_schemes_are_not_digest(self):
        assert parse_digest("Basic YmJscDoxMjM0") is None


class TestRtspRewrite:
    @pytest.mark.parametrize("code", [PERSONAL, VP_CODE])
    def test_an_accepted_code_is_signed_with_the_printers(self, code):
        head, matched, had_auth = rewrite_rtsp_auth(_request(code), ACCEPTED, PRINTER_CODE)

        assert had_auth and matched is not None and matched.code == code
        assert _response_of(head) == _signed(PRINTER_CODE)["response"]
        # Everything else stays as the slicer sent it.
        assert head.replace(_response_of(head).encode(), b"") == _request(code).replace(
            _signed(code)["response"].encode(), b""
        )

    def test_the_personal_code_names_its_user(self):
        _, matched, _ = rewrite_rtsp_auth(_request(PERSONAL), ACCEPTED, PRINTER_CODE)
        assert matched.user_id == 7

    def test_an_unknown_code_passes_unchanged(self):
        request = _request("99990000")
        assert rewrite_rtsp_auth(request, ACCEPTED, PRINTER_CODE) == (request, None, True)

    def test_a_request_without_auth_passes_unchanged(self):
        request = _request(None, method="OPTIONS", cseq=1)
        assert rewrite_rtsp_auth(request, ACCEPTED, PRINTER_CODE) == (request, None, False)


def _chamber_payload(code: str) -> bytes:
    from backend.app.services.camera import _create_chamber_auth_payload

    return _create_chamber_auth_payload(code)


class TestChamberRewrite:
    def test_an_accepted_code_is_replaced_by_the_printers(self):
        payload, matched = rewrite_chamber_auth(_chamber_payload(PERSONAL), ACCEPTED, PRINTER_CODE)
        assert payload == _chamber_payload(PRINTER_CODE)
        assert matched.user_id == 7

    def test_an_unknown_code_passes_unchanged(self):
        payload = _chamber_payload("99990000")
        assert rewrite_chamber_auth(payload, ACCEPTED, PRINTER_CODE) == (payload, None)


# -- end to end, against a printer that checks Digest itself -------------------


@pytest.fixture
def certs(tmp_path):
    from backend.app.services.virtual_printer.certificate import CertificateService

    return CertificateService(cert_dir=tmp_path, serial="09400A391800001").generate_certificates()


class FakePrinter:
    """RTSPS that answers 401 unless the Digest was made with the printer's code, and records what it got."""

    def __init__(self):
        self.received = bytearray()
        self.server = None

    async def start(self, cert_path, key_path) -> int:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=context)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            while True:
                first = await reader.readexactly(1)
                if first == b"$":
                    header = await reader.readexactly(3)
                    frame = await reader.readexactly(int.from_bytes(header[1:3], "big"))
                    self.received += first + header + frame
                    continue
                head = first + await reader.readuntil(b"\r\n\r\n")
                self.received += head
                cseq = re.search(rb"CSeq: (\d+)", head).group(1)
                auth = re.search(rb"Authorization: (.*)\r\n", head)
                ok = False
                if auth:
                    params = parse_digest(auth.group(1).decode())
                    ok = params["response"] == digest_response(params, head.split(b" ")[0].decode(), PRINTER_CODE)
                status = b"200 OK" if ok else b"401 Unauthorized"
                writer.write(b"RTSP/1.0 " + status + b"\r\nCSeq: " + cseq + b"\r\n\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def bridge(certs):
    cert_path, key_path = certs
    printer = FakePrinter()
    port = await printer.start(cert_path, key_path)

    async def codes():
        return ACCEPTED

    camera = CameraBridge(
        name="test",
        protocol=RTSP,
        listen_port=0,
        bind_address="127.0.0.1",
        target_host="127.0.0.1",
        target_port=port,
        cert_path=cert_path,
        key_path=key_path,
        codes=codes,
        printer_code=lambda: PRINTER_CODE,
        rate_limiter=AuthRateLimiter(max_attempts=3),
    )
    task = asyncio.create_task(camera.start())
    for _ in range(100):
        if camera._server is not None:
            break
        await asyncio.sleep(0.01)
    camera.listen_port = camera._server.sockets[0].getsockname()[1]
    yield camera, printer
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await printer.stop()


async def _connect(camera):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return await asyncio.open_connection("127.0.0.1", camera.listen_port, ssl=context)


async def _reply(reader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)


@pytest.mark.asyncio
async def test_a_personal_code_gets_the_stream_and_frames_pass_through(bridge):
    camera, printer = bridge
    reader, writer = await _connect(camera)

    writer.write(_request(None, method="OPTIONS", cseq=1))
    assert b"401" in await _reply(reader)  # the fake asks for auth on everything
    writer.write(_request(PERSONAL, cseq=2))
    assert b"200 OK" in await _reply(reader)
    frame = b"$\x01\x00\x04RTCP"
    writer.write(frame + _request(PERSONAL, method="GET_PARAMETER", cseq=3))
    assert b"200 OK" in await _reply(reader)
    writer.close()

    assert frame in printer.received
    assert PERSONAL.encode() not in printer.received


@pytest.mark.asyncio
async def test_unknown_codes_never_reach_the_printer_and_are_rate_limited(bridge):
    camera, printer = bridge
    for attempt in range(3):
        reader, writer = await _connect(camera)
        writer.write(_request("99990000", cseq=attempt + 2))
        # Cut off without an answer from the printer, which would say whether the guess was right.
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
    assert b"Authorization" not in printer.received
    # Three failures: the source stays out, even with a good code.
    reader, writer = await _connect(camera)
    writer.write(_request(PERSONAL))
    assert await asyncio.wait_for(reader.read(), timeout=5) == b""


def _hidden_digest(code: str) -> bytes:
    p = _signed(code)
    return (f'username="bblp", realm="{REALM}", nonce="{NONCE}", uri="{URI}", response="{p["response"]}"').encode()


@pytest.mark.parametrize(
    "request_bytes",
    [
        # A Digest header inside another header's value, which the printer's server finds too.
        lambda: (
            f"DESCRIBE {URI} RTSP/1.0\r\nCSeq: 2\r\nX-A: Authorization: Digest ".encode()
            + _hidden_digest("99990000")
            + b"\r\n\r\n"
        ),
        # A Digest header after a Basic one.
        lambda: (
            f"DESCRIBE {URI} RTSP/1.0\r\nCSeq: 2\r\nAuthorization: Basic Zm9v\r\nAuthorization: Digest ".encode()
            + _hidden_digest("99990000")
            + b"\r\n\r\n"
        ),
        # A Basic header alone: not a sign-in the bridge can re-sign.
        lambda: f"DESCRIBE {URI} RTSP/1.0\r\nCSeq: 2\r\nAuthorization: Basic Zm9v\r\n\r\n".encode(),
        # Two Digest headers, the first one accepted.
        lambda: _request(PERSONAL)[:-2] + b"Authorization: Digest " + _hidden_digest("99990000") + b"\r\n\r\n",
    ],
)
def test_a_sign_in_it_does_not_re_sign_counts_as_one(request_bytes):
    head = request_bytes()
    assert rewrite_rtsp_auth(head, ACCEPTED, PRINTER_CODE) == (head, None, True)


@pytest.mark.asyncio
async def test_codes_are_looked_up_on_a_sign_in_and_again_when_old(monkeypatch):
    from backend.app.services.virtual_printer import camera_bridge

    calls = []

    async def lookup():
        calls.append(1)
        return ACCEPTED

    clock = [1000.0]
    monkeypatch.setattr(camera_bridge.time, "monotonic", lambda: clock[0])
    codes = camera_bridge._AcceptedCodes(lookup)
    await codes.get()
    await codes.get()
    assert len(calls) == 1
    clock[0] += camera_bridge.CODES_MAX_AGE + 1
    await codes.get()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_connection_that_sends_nothing_costs_no_lookup(certs, monkeypatch):
    from backend.app.services.virtual_printer import camera_bridge

    monkeypatch.setattr(camera_bridge, "FIRST_BYTES_TIMEOUT", 0.2)
    cert_path, key_path = certs
    looked_up = []

    async def codes():
        looked_up.append(1)
        return ACCEPTED

    camera = CameraBridge(
        name="test",
        protocol=RTSP,
        listen_port=0,
        bind_address="127.0.0.1",
        target_host="127.0.0.1",
        target_port=1,  # nothing listens: a connection attempt would fail the test below
        cert_path=cert_path,
        key_path=key_path,
        codes=codes,
        printer_code=lambda: PRINTER_CODE,
    )
    task = asyncio.create_task(camera.start())
    for _ in range(100):
        if camera._server is not None:
            break
        await asyncio.sleep(0.01)
    camera.listen_port = camera._server.sockets[0].getsockname()[1]
    try:
        reader, _ = await _connect(camera)
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        assert looked_up == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_chamber_sign_in_uses_the_printers_code(certs):
    cert_path, key_path = certs
    got = asyncio.get_running_loop().create_future()

    async def printer_side(reader, writer):
        got.set_result(await reader.readexactly(80))
        writer.write(b"jpeg")
        await writer.drain()
        writer.close()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server = await asyncio.start_server(printer_side, "127.0.0.1", 0, ssl=context)

    async def codes():
        return ACCEPTED

    camera = CameraBridge(
        name="test",
        protocol=CHAMBER,
        listen_port=0,
        bind_address="127.0.0.1",
        target_host="127.0.0.1",
        target_port=server.sockets[0].getsockname()[1],
        cert_path=cert_path,
        key_path=key_path,
        codes=codes,
        printer_code=lambda: PRINTER_CODE,
    )
    task = asyncio.create_task(camera.start())
    for _ in range(100):
        if camera._server is not None:
            break
        await asyncio.sleep(0.01)
    camera.listen_port = camera._server.sockets[0].getsockname()[1]
    try:
        reader, writer = await _connect(camera)
        writer.write(_chamber_payload(PERSONAL))
        await writer.drain()
        assert await asyncio.wait_for(got, timeout=5) == _chamber_payload(PRINTER_CODE)
        assert await asyncio.wait_for(reader.read(4), timeout=5) == b"jpeg"
        writer.close()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_stopping_ends_a_connection_still_open(bridge):
    """The virtual printer stops even while a slicer is watching the camera."""
    camera, _ = bridge
    reader, writer = await _connect(camera)
    writer.write(_request(PERSONAL))
    assert b"200 OK" in await _reply(reader)

    await asyncio.wait_for(camera.stop(), timeout=5)
    assert await asyncio.wait_for(reader.read(), timeout=5) == b""


@pytest.mark.asyncio
async def test_cancelling_the_bridge_ends_a_connection_still_open(certs):
    """Cancelling the listening task must not wait for the slicer to hang up."""
    cert_path, key_path = certs
    printer = FakePrinter()
    port = await printer.start(cert_path, key_path)

    async def codes():
        return ACCEPTED

    camera = CameraBridge(
        name="test",
        protocol=RTSP,
        listen_port=0,
        bind_address="127.0.0.1",
        target_host="127.0.0.1",
        target_port=port,
        cert_path=cert_path,
        key_path=key_path,
        codes=codes,
        printer_code=lambda: PRINTER_CODE,
    )
    task = asyncio.create_task(camera.start())
    for _ in range(100):
        if camera._server is not None:
            break
        await asyncio.sleep(0.01)
    camera.listen_port = camera._server.sockets[0].getsockname()[1]
    try:
        reader, writer = await _connect(camera)
        writer.write(_request(PERSONAL))
        assert b"200 OK" in await _reply(reader)

        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
    finally:
        await printer.stop()
