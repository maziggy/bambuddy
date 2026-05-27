"""Tests for the camera TLS proxy and RTSP URL rewriting."""

import asyncio

import pytest

from backend.app.services.camera import create_tls_proxy, rewrite_rtsp_request_url


class TestRewriteRtspRequestUrl:
    """Tests for RTSP request-line URL rewriting."""

    def test_rewrites_describe_request_line(self):
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        data = b"DESCRIBE rtsp://127.0.0.1:45221/streaming/live/1 RTSP/1.0\r\nCSeq: 1\r\n\r\n"
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)

        assert b"DESCRIBE rtsps://192.168.1.100:322/streaming/live/1 RTSP/1.0\r\n" in result

    def test_rewrites_setup_request_line(self):
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        data = b"SETUP rtsp://127.0.0.1:45221/streaming/live/1/trackID=0 RTSP/1.0\r\nCSeq: 3\r\n\r\n"
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)

        assert b"SETUP rtsps://192.168.1.100:322/streaming/live/1/trackID=0 RTSP/1.0\r\n" in result

    def test_rewrites_play_request_line(self):
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        data = b"PLAY rtsp://127.0.0.1:45221/streaming/live/1 RTSP/1.0\r\nCSeq: 5\r\n\r\n"
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)

        assert b"PLAY rtsps://192.168.1.100:322/streaming/live/1 RTSP/1.0\r\n" in result

    def test_preserves_authorization_header(self):
        """Digest auth embeds the URI in a hash — rewriting it breaks auth."""
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        data = (
            b"DESCRIBE rtsp://127.0.0.1:45221/streaming/live/1 RTSP/1.0\r\n"
            b"CSeq: 2\r\n"
            b'Authorization: Digest username="bblp", '
            b'uri="rtsp://127.0.0.1:45221/streaming/live/1", '
            b'response="abc123"\r\n'
            b"\r\n"
        )
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)

        # Request line IS rewritten
        assert b"DESCRIBE rtsps://192.168.1.100:322/streaming/live/1 RTSP/1.0\r\n" in result
        # Authorization header is NOT rewritten
        assert b'uri="rtsp://127.0.0.1:45221/streaming/live/1"' in result
        assert b'response="abc123"' in result

    def test_no_rewrite_on_non_rtsp_data(self):
        """Binary RTP data and other non-RTSP data should pass through unchanged."""
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        # Interleaved RTP data (starts with $)
        data = b"$\x00\x00\x10" + b"\x00" * 16
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)
        assert result == data

    def test_no_rewrite_on_empty_data(self):
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        assert rewrite_rtsp_request_url(b"", proxy_url, real_url) == b""

    def test_only_first_rtsp_line_rewritten(self):
        """If somehow multiple RTSP/1.0 lines exist, only the first is rewritten."""
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        data = (
            b"DESCRIBE rtsp://127.0.0.1:45221/streaming/live/1 RTSP/1.0\r\n"
            b"CSeq: 1\r\n"
            b"X-Custom: rtsp://127.0.0.1:45221/other RTSP/1.0\r\n"
            b"\r\n"
        )
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)

        lines = result.split(b"\r\n")
        # First line rewritten
        assert lines[0] == b"DESCRIBE rtsps://192.168.1.100:322/streaming/live/1 RTSP/1.0"
        # Hypothetical other line NOT rewritten
        assert lines[2] == b"X-Custom: rtsp://127.0.0.1:45221/other RTSP/1.0"

    def test_preserves_crlf_structure(self):
        proxy_url = b"rtsp://127.0.0.1:45221"
        real_url = b"rtsps://192.168.1.100:322"

        data = b"DESCRIBE rtsp://127.0.0.1:45221/streaming/live/1 RTSP/1.0\r\nCSeq: 1\r\n\r\n"
        result = rewrite_rtsp_request_url(data, proxy_url, real_url)

        # Must still end with double CRLF (empty line terminates headers)
        assert result.endswith(b"\r\n\r\n")
        # Must have CSeq intact
        assert b"CSeq: 1\r\n" in result


class TestCreateTlsProxy:
    """Tests for TLS proxy server lifecycle."""

    @pytest.mark.asyncio
    async def test_proxy_returns_port_and_server(self):
        """Verify proxy creates a listening server on an ephemeral port."""
        # Use a non-routable target — we just test the server starts, not the TLS connection
        port, server = await create_tls_proxy("192.0.2.1", 322)

        assert isinstance(port, int)
        assert port > 0
        assert server.is_serving()

        server.close()
        await server.wait_closed()

    @pytest.mark.asyncio
    async def test_proxy_accepts_connection(self):
        """Verify proxy accepts TCP connections (TLS to target will fail, but accept works)."""
        port, server = await create_tls_proxy("192.0.2.1", 322)

        try:
            # Connect to the proxy — it should accept the connection
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port),
                timeout=2.0,
            )
            # The proxy will try to connect to 192.0.2.1:322 (non-routable), fail,
            # and close our connection. That's expected.
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, TimeoutError):
            pass  # Expected — target is unreachable

        server.close()
        await server.wait_closed()

    @pytest.mark.asyncio
    async def test_proxy_cleanup(self):
        """Verify proxy stops serving after close."""
        port, server = await create_tls_proxy("192.0.2.1", 322)
        assert server.is_serving()

        server.close()
        await server.wait_closed()

        assert not server.is_serving()


class TestForwardersCatchRuntimeError:
    """Regression contract: the bidirectional forwarders inside ``_handle``
    must catch ``RuntimeError``, not just the connection-error tuple.

    asyncio's default selector event loop reports a write-to-closed-handle as
    ``ConnectionResetError`` / ``OSError``. uvloop (which is what runs under
    uvicorn's ``--loop uvloop`` / when ``uvloop`` is installed) raises a plain
    ``RuntimeError`` from ``UVHandle._ensure_alive``. If the except clause
    drops ``RuntimeError`` the handler escapes the forwarder, asyncio's
    ``client_connected_cb`` task-exception handler logs an "Unhandled
    exception" stack, and the user sees noise like:

        ERROR [asyncio] Unhandled exception in client_connected_cb
        ...
        RuntimeError: unable to perform operation on
                      <TCPTransport closed=True ...>; the handler is closed

    Regression guard for that path. Source-level check rather than a runtime
    test because the forwarders are nested closures inside ``_handle`` and
    extracting them just for testability would require a pure-cosmetic
    refactor of the proxy.
    """

    def test_fwd_to_server_catches_runtime_error(self):
        import inspect

        src = inspect.getsource(create_tls_proxy)
        fwd_section = src.split("async def _fwd_to_server")[1].split("async def _fwd_to_client")[0]
        assert "RuntimeError" in fwd_section, (
            "_fwd_to_server must catch RuntimeError to absorb uvloop's "
            "write-to-closed-handle error; otherwise it leaks to "
            "asyncio.client_connected_cb's unhandled-exception logger."
        )

    def test_fwd_to_client_catches_runtime_error(self):
        import inspect

        src = inspect.getsource(create_tls_proxy)
        # Slice from `_fwd_to_client` to `await asyncio.gather` so we only
        # inspect that closure's body.
        fwd_section = src.split("async def _fwd_to_client")[1].split("await asyncio.gather")[0]
        assert "RuntimeError" in fwd_section, (
            "_fwd_to_client must catch RuntimeError — that's the actual frame "
            "in the original bug report (camera.py:191 dst.write(data) under "
            "uvloop)."
        )
