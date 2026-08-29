"""The RTSPS proxy must not leave a handler running past its server (#2968).

The reporter's log carries three of these, one per camera snapshot, at ERROR
with a traceback pointing into ``camera.py``:

    ERROR [asyncio] Task was destroyed but it is pending!
    task: <Task pending name='Task-1889625'
      coro=<create_tls_proxy.<locals>._handle() done, defined at camera.py:243>
      wait_for=<_GatheringFuture pending ...>>

``asyncio.start_server`` wraps the connection callback in a task and keeps only
a weak reference to it, so a handler still awaiting its two forwarders can be
collected while pending -- which is exactly what that message is. Nothing was
broken by it (the snapshot on either side of each one succeeded), but it reads
like a camera fault in a log people attach to bug reports, and the shape behind
it is real: teardown closed the listener and then waited on handlers that only
finish when the *peer* drops the socket.

Two things fix it. The handlers are strongly referenced for as long as they run,
and ``close_tls_proxy`` cancels them rather than hoping ffmpeg has already gone.

The upstream here is a real TLS listener rather than a bare socket, because the
proxy spends its first ten seconds inside ``open_connection``: a stand-in that
never completes a handshake never reaches the forwarding state these tests are
about. The proxy sets ``CERT_NONE`` (Bambu printers are self-signed), so a
throwaway certificate is all it takes.
"""

from __future__ import annotations

import asyncio
import datetime
import gc
import logging
import ssl

import pytest

from backend.app.services.camera import close_tls_proxy, create_tls_proxy


@pytest.fixture(scope="module")
def self_signed_cert(tmp_path_factory):
    """Certificate and key for the stand-in printer, generated once."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )

    directory = tmp_path_factory.mktemp("tls")
    cert_file = directory / "cert.pem"
    key_file = directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


async def _printer(self_signed_cert, on_data=None) -> tuple[asyncio.Server, int]:
    """A TLS listener standing in for the printer's RTSPS port.

    Accepts, hands anything it receives to *on_data*, and otherwise waits --
    which is the state the upstream is in while ffmpeg is being reaped.
    """

    async def _accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                if on_data is not None:
                    on_data(data)
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    cert_file, key_file = self_signed_cert
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))
    server = await asyncio.start_server(_accept, "127.0.0.1", 0, ssl=context)
    return server, server.sockets[0].getsockname()[1]


async def _close(proxy) -> None:
    """Teardown, bounded.

    Every close in this file goes through the timeout, including the ones in
    ``finally`` blocks that are only there to tidy up. Losing the cancellation
    or the handler tracking makes ``close_tls_proxy`` wait on a peer that is
    not going to drop, and an unbounded await turns that regression into a
    hung suite instead of a failing test.
    """
    await asyncio.wait_for(close_tls_proxy(proxy), timeout=5.0)


async def _shutdown(server: asyncio.Server) -> None:
    """Bounded teardown for the stand-in printer.

    ``wait_closed`` waits for the listener's own handlers, and one of those is
    reading a socket the proxy still holds. Left unbounded it inherits any
    regression in the proxy's teardown and hangs the suite in a second place.
    """
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=5.0)
    except asyncio.TimeoutError:
        pass


async def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """Poll rather than sleep a fixed amount: these are real sockets."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


class TestTheHandlerIsHeldWhileItRuns:
    @pytest.mark.asyncio
    async def test_an_open_connection_is_tracked(self, self_signed_cert):
        """The set is the strong reference asyncio does not keep."""
        upstream, upstream_port = await _printer(self_signed_cert)
        try:
            port, proxy = await create_tls_proxy("127.0.0.1", upstream_port)
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                assert await _wait_for(lambda: len(proxy._bambuddy_proxy_handlers) == 1)
                assert not next(iter(proxy._bambuddy_proxy_handlers)).done()

                writer.close()
            finally:
                await _close(proxy)
        finally:
            await _shutdown(upstream)

    @pytest.mark.asyncio
    async def test_a_finished_handler_is_released(self, self_signed_cert):
        """Tracked for the connection's life, not the process's -- a long
        stream must not accumulate one entry per reconnect."""
        upstream, upstream_port = await _printer(self_signed_cert)
        try:
            port, proxy = await create_tls_proxy("127.0.0.1", upstream_port)
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                assert await _wait_for(lambda: len(proxy._bambuddy_proxy_handlers) == 1)

                writer.close()

                assert await _wait_for(lambda: proxy._bambuddy_proxy_handlers == set())
            finally:
                await _close(proxy)
        finally:
            await _shutdown(upstream)


class TestCloseDoesNotDependOnThePeer:
    @pytest.mark.asyncio
    async def test_a_live_connection_does_not_stall_the_close(self, self_signed_cert):
        """``server.close()`` leaves established connections running, so the
        old close/wait pair finished only when the client happened to drop.
        Here the client is still attached and close still returns."""
        upstream, upstream_port = await _printer(self_signed_cert)
        try:
            port, proxy = await create_tls_proxy("127.0.0.1", upstream_port)
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await _wait_for(lambda: len(proxy._bambuddy_proxy_handlers) == 1)

            await _close(proxy)

            assert proxy._bambuddy_proxy_handlers == set()
            writer.close()
        finally:
            await _shutdown(upstream)

    @pytest.mark.asyncio
    async def test_no_handler_survives_the_close(self, self_signed_cert, caplog):
        """The actual complaint: nothing is left pending for the garbage
        collector to shout about afterwards."""
        upstream, upstream_port = await _printer(self_signed_cert)
        try:
            port, proxy = await create_tls_proxy("127.0.0.1", upstream_port)
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await _wait_for(lambda: len(proxy._bambuddy_proxy_handlers) == 1)
            handler = next(iter(proxy._bambuddy_proxy_handlers))

            with caplog.at_level(logging.ERROR, logger="asyncio"):
                await _close(proxy)
                writer.close()
                await asyncio.sleep(0.05)
                gc.collect()
                await asyncio.sleep(0.05)

            assert handler.done()
            assert not [r for r in caplog.records if "Task was destroyed" in r.getMessage()]
        finally:
            await _shutdown(upstream)

    @pytest.mark.asyncio
    async def test_closing_twice_is_harmless(self, self_signed_cert):
        """Both callers reach their finally block on the error paths too."""
        upstream, upstream_port = await _printer(self_signed_cert)
        try:
            _, proxy = await create_tls_proxy("127.0.0.1", upstream_port)
            await _close(proxy)
            await _close(proxy)
        finally:
            await _shutdown(upstream)

    @pytest.mark.asyncio
    async def test_it_works_on_a_server_it_did_not_create(self):
        """Degrades to the close/wait it replaces rather than raising."""
        plain = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)

        await _close(plain)

        assert not plain.is_serving()


@pytest.mark.asyncio
async def test_the_proxy_still_forwards(self_signed_cert):
    """The teardown changes must not cost the proxy its job: plain TCP in one
    end, TLS to the printer out the other."""
    received: list[bytes] = []
    upstream, upstream_port = await _printer(self_signed_cert, on_data=received.append)
    try:
        port, proxy = await create_tls_proxy("127.0.0.1", upstream_port)
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"OPTIONS rtsp://127.0.0.1/streaming/live/1 RTSP/1.0\r\n\r\n")
            await writer.drain()

            assert await _wait_for(lambda: bool(received))
            assert b"OPTIONS" in received[0]

            writer.close()
        finally:
            await _close(proxy)
    finally:
        await _shutdown(upstream)
