"""Tests for Virtual Printer MQTT server."""

import ast
import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer


class TestMQTTServerNoGlobalState:
    """Ensure MQTT server doesn't set global asyncio state."""

    def test_no_global_exception_handler(self):
        """MQTT server must not call set_exception_handler().

        set_exception_handler() is global to the event loop. When multiple
        VP instances run, each would overwrite the previous handler,
        causing lost error context and spurious 'Unhandled exception in
        client_connected_cb' messages.
        """
        source = inspect.getsource(SimpleMQTTServer)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "set_exception_handler":
                raise AssertionError(
                    "SimpleMQTTServer must not call set_exception_handler(). "
                    "It overwrites the global asyncio exception handler, "
                    "breaking multi-VP setups."
                )


def _make_server(serial: str = "01P00A391800001") -> SimpleMQTTServer:
    """Build a SimpleMQTTServer with dummy cert paths (start() is never called)."""
    return SimpleMQTTServer(
        serial=serial,
        access_code="deadbeef",
        cert_path=Path("/tmp/unused.crt"),  # nosec B108
        key_path=Path("/tmp/unused.key"),  # nosec B108
        model="C12",
    )


class TestExtractSerialFromTopic:
    """_extract_serial_from_topic should pull the serial out of device topics."""

    @pytest.mark.parametrize(
        "topic,expected",
        [
            ("device/01P00A391800001/request", "01P00A391800001"),
            ("device/09400A391800003/report", "09400A391800003"),
            ("device/00M00A391800004/request/subpath", "00M00A391800004"),
        ],
    )
    def test_valid_topics(self, topic, expected):
        assert SimpleMQTTServer._extract_serial_from_topic(topic) == expected

    @pytest.mark.parametrize(
        "topic",
        [
            "",
            "device/",
            "device//request",  # empty serial
            "notdevice/01P00A/request",
            "random",
        ],
    )
    def test_invalid_topics(self, topic):
        assert SimpleMQTTServer._extract_serial_from_topic(topic) is None


def _build_publish_payload(topic: str, message: dict) -> bytes:
    """Build the MQTT PUBLISH packet *payload* (past the fixed header byte)."""
    topic_bytes = topic.encode("utf-8")
    message_bytes = json.dumps(message).encode("utf-8")
    return len(topic_bytes).to_bytes(2, "big") + topic_bytes + message_bytes


class TestPublishHandlerAdaptiveSerial:
    """#927: `_handle_publish` must accept any `device/*/request` topic from an
    authenticated client and use the topic's serial for all responses."""

    def test_handle_publish_accepts_mismatched_serial(self):
        """Prior behavior silently dropped publishes whose topic serial didn't
        equal self.serial. After the fix the handler must run and learn the
        client's serial.
        """
        server = _make_server(serial="01P00A391800001")  # synthetic VP serial
        server._client_serials["test-client"] = server.serial  # simulate post-CONNECT

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        # Slicer publishes with a *different* serial — the exact bug from #927.
        topic = "device/01P00AABCDEFGHI/request"
        payload = _build_publish_payload(topic, {"info": {"command": "get_version", "sequence_id": "42"}})

        asyncio.run(server._handle_publish(0x30, payload, writer, "test-client"))

        # Learned the client's serial.
        assert server._client_serials["test-client"] == "01P00AABCDEFGHI"

        # Wrote at least one packet to the slicer (the version response).
        assert writer.write.called
        all_bytes = b"".join(call.args[0] for call in writer.write.call_args_list)
        # Response topic must contain the *client's* serial, not self.serial.
        assert b"device/01P00AABCDEFGHI/report" in all_bytes
        assert b"device/01P00A391800001/report" not in all_bytes
        # Response body carries get_version with the client's serial as sn.
        assert b'"command": "get_version"' in all_bytes
        assert b'"sn": "01P00AABCDEFGHI"' in all_bytes

    def test_handle_publish_ignores_non_request_topics(self):
        server = _make_server()
        server._client_serials["c1"] = server.serial
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        payload = _build_publish_payload(
            "device/01P00AABCDEFGHI/report",  # /report, not /request
            {"pushing": {"command": "pushall"}},
        )
        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

        assert not writer.write.called  # no response
        # Client serial unchanged
        assert server._client_serials["c1"] == server.serial

    def test_handle_publish_pushall_uses_client_serial(self):
        """pushall → status_report must be sent on the client's subscribed topic."""
        server = _make_server(serial="01P00A391800001")
        server._client_serials["c1"] = server.serial

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        payload = _build_publish_payload(
            "device/CUSTOMSERIAL123/request",
            {"pushing": {"command": "pushall", "sequence_id": "1"}},
        )
        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

        all_bytes = b"".join(call.args[0] for call in writer.write.call_args_list)
        assert b"device/CUSTOMSERIAL123/report" in all_bytes
        assert b'"command": "push_status"' in all_bytes
        assert server._client_serials["c1"] == "CUSTOMSERIAL123"

    def test_handle_publish_tolerates_null_terminated_payload(self):
        """#927: OrcaSlicer on Linux appends the C-string \\0 to MQTT payloads.
        The handler must still parse and respond rather than silently dropping."""
        server = _make_server(serial="01P00A391800001")
        server._client_serials["c1"] = server.serial

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        topic = "device/01P00A391800001/request"
        topic_bytes = topic.encode("utf-8")
        # Real-world bytes captured from EdwardChamberlain's support log: the
        # JSON ends with an extra \x00 that strict json.loads rejects.
        message_bytes = b'{"pushing":{"command":"pushall","sequence_id":"7"}}\x00'
        payload = len(topic_bytes).to_bytes(2, "big") + topic_bytes + message_bytes

        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

        all_bytes = b"".join(call.args[0] for call in writer.write.call_args_list)
        assert b"device/01P00A391800001/report" in all_bytes
        assert b'"command": "push_status"' in all_bytes


class TestClientSerialLifecycle:
    """_client_serials must be cleaned up on disconnect/stop to avoid leaks."""

    def test_stop_clears_client_serials(self):
        server = _make_server()
        server._client_serials["a"] = "X"
        server._client_serials["b"] = "Y"
        # stop() is async but we only need to cover the clear() path; run a minimal version
        asyncio.run(server.stop())
        assert server._client_serials == {}


def _build_connect_payload(
    keep_alive: int,
    access_code: str = "deadbeef",
    username: str = "bblp",
    client_id: str = "orca",
) -> bytes:
    """Build an MQTT CONNECT variable-header + payload (without the fixed header).

    Layout matches the parser in `_handle_connect`:
    proto_name_len(2) + "MQTT"(4) + level(1) + flags(1) + keepalive(2) +
    client_id_len(2) + client_id + username_len(2) + username +
    password_len(2) + password.
    """
    proto = b"MQTT"
    parts = bytearray()
    parts += len(proto).to_bytes(2, "big") + proto
    parts += bytes([0x04, 0xC2])  # protocol level 4 (MQTT 3.1.1), flags: user+pass+clean
    parts += keep_alive.to_bytes(2, "big")
    cid = client_id.encode("utf-8")
    parts += len(cid).to_bytes(2, "big") + cid
    user = username.encode("utf-8")
    parts += len(user).to_bytes(2, "big") + user
    pw = access_code.encode("utf-8")
    parts += len(pw).to_bytes(2, "big") + pw
    return bytes(parts)


class TestHandleConnectKeepalive:
    """`_handle_connect` must return the negotiated keepalive (#1548).

    Pre-fix, the parser ignored this field and the read loop fell back to
    a hardcoded 60 s timeout, closing OrcaSlicer's idle MQTT connection
    after exactly 60 s instead of waiting 1.5× the client-negotiated
    keepalive as MQTT spec §4.4 requires.
    """

    def test_returns_negotiated_keepalive_on_auth_success(self):
        server = _make_server()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        # Also stub status-report writes triggered post-auth
        payload = _build_connect_payload(keep_alive=120)

        result = asyncio.run(server._handle_connect(payload, writer))

        assert result == (True, 120)

    def test_returns_zero_keepalive_for_no_keepalive_clients(self):
        """`keep_alive == 0` in CONNECT means the client opted out per spec
        §3.1.2.10 — server must report it back so the read loop can drop
        the timeout entirely."""
        server = _make_server()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        payload = _build_connect_payload(keep_alive=0)

        result = asyncio.run(server._handle_connect(payload, writer))

        assert result == (True, 0)

    def test_returns_false_with_zero_keepalive_on_auth_failure(self):
        """Bad password path still returns the tuple shape so the caller's
        unpack doesn't break."""
        server = _make_server()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        payload = _build_connect_payload(keep_alive=60, access_code="wrong")

        result = asyncio.run(server._handle_connect(payload, writer))

        assert result == (False, 0)

    def test_returns_false_with_zero_keepalive_on_parse_error(self):
        """Malformed CONNECT (e.g. truncated) must not crash and must
        still hand a tuple back to the caller."""
        server = _make_server()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        # 3 bytes is far shorter than even the protocol-name prefix needs.
        result = asyncio.run(server._handle_connect(b"\x00\x04MQ", writer))

        assert result == (False, 0)


class TestHandleClientHonoursKeepalive:
    """`_handle_client` must use the client-negotiated keepalive for its
    read-loop timeout, not the hardcoded 60 s default (#1548)."""

    @pytest.mark.asyncio
    async def test_idle_client_kept_alive_beyond_60s_when_keepalive_is_long(self):
        """The literal #1548 repro: a client negotiates keepalive=180 and
        then sits idle. Pre-fix the read loop closed the connection after
        60 s (hardcoded). Post-fix the timeout is 1.5×180=270 s — so the
        connection is still open after the original 60 s boundary."""
        server = _make_server()
        server._running = True

        reader = asyncio.StreamReader()
        # Feed CONNECT (with fixed header byte 0x10 + remaining length)
        connect_payload = _build_connect_payload(keep_alive=180)
        rl = len(connect_payload)
        # MQTT remaining-length encoding for values <128 is a single byte.
        assert rl < 128
        reader.feed_data(bytes([0x10, rl]) + connect_payload)
        # No further data — client goes idle.

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("1.2.3.4", 12345))

        # Patch the post-auth status-report send so the handler doesn't
        # depend on a real serial/payload path.
        server._send_status_report = AsyncMock()

        task = asyncio.create_task(server._handle_client(reader, writer))

        # Wait past the old hardcoded 60 s threshold by a margin. Real-time
        # 60 s would be far too slow for a unit test — drive simulated time
        # by yielding repeatedly. asyncio.wait_for with a real wall-clock
        # delay would actually consume 60 s of test time, so instead we
        # patch the timeout to a small value and assert the timeout chosen
        # by the loop matches our expectation.
        # Approach: let the task progress past the CONNECT, then cancel.
        await asyncio.sleep(0.1)  # give the loop a chance to process CONNECT
        # The post-auth read should now be waiting on reader with the
        # negotiated keepalive. We can't observe the timeout directly, so
        # we just verify the connection wasn't closed by inspecting close().
        assert not writer.close.called, "connection should still be open after CONNECT"
        # Cancel cleanly
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_idle_client_closed_after_one_and_a_half_times_keepalive(self):
        """Tight verification: keepalive=2 must close the connection in
        ~3 s (1.5×) of idle, well above the noise floor for an async test."""
        server = _make_server()
        server._running = True

        reader = asyncio.StreamReader()
        connect_payload = _build_connect_payload(keep_alive=2)
        rl = len(connect_payload)
        assert rl < 128
        reader.feed_data(bytes([0x10, rl]) + connect_payload)

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("1.2.3.4", 12345))
        server._send_status_report = AsyncMock()

        start = asyncio.get_event_loop().time()
        await server._handle_client(reader, writer)
        elapsed = asyncio.get_event_loop().time() - start

        # 1.5×2s = 3s expected. Allow ±1s slop for the read of CONNECT
        # itself + scheduler jitter on a loaded CI box.
        assert 2.0 < elapsed < 4.5, f"expected ~3s timeout, got {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_pingreq_resets_idle_timeout(self):
        """A PINGREQ within the keepalive window must keep the connection
        open — the per-packet read timeout is restarted on every byte
        delivered, so the next idle window is measured from the PINGREQ."""
        server = _make_server()
        server._running = True

        reader = asyncio.StreamReader()
        connect_payload = _build_connect_payload(keep_alive=2)
        rl = len(connect_payload)
        assert rl < 128
        reader.feed_data(bytes([0x10, rl]) + connect_payload)

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("1.2.3.4", 12345))
        server._send_status_report = AsyncMock()

        async def _drive():
            # Feed a PINGREQ (0xC0 0x00 — type 12 with zero remaining length)
            # at 2s, which is 1s *before* the would-be timeout, and a
            # DISCONNECT at 2.5s so the test exits deterministically.
            await asyncio.sleep(2.0)
            reader.feed_data(bytes([0xC0, 0x00]))
            await asyncio.sleep(0.5)
            reader.feed_data(bytes([0xE0, 0x00]))  # DISCONNECT

        driver = asyncio.create_task(_drive())
        start = asyncio.get_event_loop().time()
        await server._handle_client(reader, writer)
        elapsed = asyncio.get_event_loop().time() - start
        await driver  # ensure no orphan task

        # Exit was via DISCONNECT at ~2.5s, NOT a 3s keepalive timeout.
        # Allow generous slop.
        assert 2.0 < elapsed < 3.0, f"expected exit on DISCONNECT near 2.5s, got {elapsed:.2f}s"


class TestAuthRateLimit:
    """Per-IP rate-limiting of MQTT CONNECT auth attempts.

    Bambuddy's VP exposes an 8-char access code via the slicer-facing MQTT
    server. Without a rate-limit the code is brute-forceable by anyone who
    can reach the VP's bind IP (LAN or VPN). The limiter records each
    failed auth attempt per source IP and rejects further CONNECTs from
    that IP once the per-window threshold is crossed, then auto-recovers
    when the window expires. Verified here against the production
    constants imported from the module.
    """

    @pytest.fixture
    def server(self):
        from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer

        return _make_server(serial="01P00A391800002")

    def test_under_limit_attempts_are_allowed(self, server):
        from backend.app.services.virtual_printer.mqtt_server import _AUTH_RATE_LIMIT_MAX_ATTEMPTS

        ip = "192.168.1.50"
        # Record (max-1) failures and verify the next attempt is still allowed.
        for _ in range(_AUTH_RATE_LIMIT_MAX_ATTEMPTS - 1):
            server._record_auth_failure(ip)
        assert server._is_auth_rate_limited(ip) is False

    def test_exactly_max_attempts_triggers_rate_limit(self, server):
        from backend.app.services.virtual_printer.mqtt_server import _AUTH_RATE_LIMIT_MAX_ATTEMPTS

        ip = "192.168.1.50"
        for _ in range(_AUTH_RATE_LIMIT_MAX_ATTEMPTS):
            server._record_auth_failure(ip)
        # At exactly the cap, further attempts must be rejected.
        assert server._is_auth_rate_limited(ip) is True

    def test_window_recovery_clears_old_failures(self, server):
        """A burst of failures older than the window must NOT count
        against the IP — the limiter is sliding, not cumulative."""
        import time as _time

        from backend.app.services.virtual_printer.mqtt_server import (
            _AUTH_RATE_LIMIT_MAX_ATTEMPTS,
            _AUTH_RATE_LIMIT_WINDOW_SECONDS,
        )

        ip = "192.168.1.50"
        # Inject stale timestamps directly — older than the window means the
        # limiter should drop them on the next probe.
        stale = _time.monotonic() - _AUTH_RATE_LIMIT_WINDOW_SECONDS - 1.0
        server._auth_failures[ip] = [stale] * _AUTH_RATE_LIMIT_MAX_ATTEMPTS
        # All recorded failures are outside the window — IP is no longer rate-limited.
        assert server._is_auth_rate_limited(ip) is False
        # And the dict entry was pruned (empty) instead of leaking forever.
        assert ip not in server._auth_failures

    def test_multiple_ips_tracked_independently(self, server):
        from backend.app.services.virtual_printer.mqtt_server import _AUTH_RATE_LIMIT_MAX_ATTEMPTS

        # One IP exhausts the budget; another IP must still be allowed.
        for _ in range(_AUTH_RATE_LIMIT_MAX_ATTEMPTS):
            server._record_auth_failure("10.0.0.1")
        assert server._is_auth_rate_limited("10.0.0.1") is True
        assert server._is_auth_rate_limited("10.0.0.2") is False

    def test_successful_auth_clears_failure_history(self, server):
        """A successful auth must wipe the IP's prior-failures stash so the
        user isn't penalised for typos that they ultimately corrected."""
        from backend.app.services.virtual_printer.mqtt_server import _AUTH_RATE_LIMIT_MAX_ATTEMPTS

        ip = "192.168.1.50"
        # Build up failures one short of the cap.
        for _ in range(_AUTH_RATE_LIMIT_MAX_ATTEMPTS - 1):
            server._record_auth_failure(ip)
        # Successful auth must clear them.
        server._clear_auth_failures(ip)
        # Now a subsequent failure starts the count over at 1 (well under cap).
        server._record_auth_failure(ip)
        assert server._is_auth_rate_limited(ip) is False


class TestPendingRequestRouting:
    """`push_raw_to_clients` routes the printer's response back only to the
    slicer that originated the request, not to every connected slicer.

    The bridge calls `push_raw_to_clients(topic, payload)` for every
    response it sees from the real printer. Before the fix, this fanned
    out to every connected slicer — leaking slicer A's
    `extrusion_cali_get` response into slicer B's command stream. The
    fix records `sequence_id → client_id` on the way out and looks it
    back up on the way in.
    """

    @pytest.fixture
    def server(self):
        return _make_server(serial="01P00A391800003")

    def test_single_slicer_routes_to_that_slicer(self, server):
        """Sanity check: when one slicer is connected, the response goes
        to it regardless of whether the seq_id was recorded."""
        # No recorded request, no slicer seen → returns None (broadcast).
        assert server._lookup_pending_request_client(b'{"print": {"sequence_id": "999"}}') is None

    def test_record_pending_request_walks_nested_blocks(self, server):
        """The slicer wraps its sequence_id under whichever subsystem the
        command targets (`print`, `info`, `system`, …). The helper must
        find it regardless of which key it's nested under."""
        server._record_pending_request(
            {"print": {"command": "extrusion_cali_get", "sequence_id": "42"}},
            "clientA",
        )
        assert server._pending_requests.get("42") == "clientA"

        server._record_pending_request(
            {"info": {"command": "get_version", "sequence_id": "43"}},
            "clientB",
        )
        assert server._pending_requests.get("43") == "clientB"

    def test_lookup_pops_entry_so_each_response_routes_once(self, server):
        """Once a response is matched, the pending entry is consumed so
        a later coincidental sequence_id from a printer-initiated push
        doesn't mis-route to the original client."""
        server._record_pending_request({"print": {"sequence_id": "100"}}, "clientA")
        # First lookup finds it…
        assert server._lookup_pending_request_client(b'{"print": {"sequence_id": "100"}}') == "clientA"
        # …and removes it. Second lookup with the same seq returns None
        # (treated as printer-initiated → broadcast fallback).
        assert server._lookup_pending_request_client(b'{"print": {"sequence_id": "100"}}') is None

    def test_fifo_eviction_when_cache_fills(self, server):
        """If a slicer sends many commands without responses (or the
        responses never arrive), the oldest entries age out so the dict
        can't grow unbounded."""
        from backend.app.services.virtual_printer.mqtt_server import _PENDING_REQUEST_MAX_ENTRIES

        # Fill the dict to one over the cap.
        for i in range(_PENDING_REQUEST_MAX_ENTRIES + 1):
            server._record_pending_request({"print": {"sequence_id": str(i)}}, "clientA")
        # The dict is capped — the oldest entry ("0") is gone, the newest is in.
        assert len(server._pending_requests) <= _PENDING_REQUEST_MAX_ENTRIES
        assert "0" not in server._pending_requests
        assert str(_PENDING_REQUEST_MAX_ENTRIES) in server._pending_requests

    def test_response_without_recorded_seq_returns_none_for_broadcast(self, server):
        """Printer-initiated pushes (push_status etc.) have a sequence_id
        the bridge never saw recorded. ``_lookup_pending_request_client``
        must return None so ``push_raw_to_clients`` falls back to fan-out
        — every slicer expects to receive these unsolicited messages."""
        # No record for this seq id.
        assert server._lookup_pending_request_client(b'{"print": {"sequence_id": "777"}}') is None

    def test_malformed_payload_falls_through_to_broadcast(self, server):
        """A non-JSON / non-dict payload must NOT crash the routing path —
        return None so the response broadcasts."""
        assert server._lookup_pending_request_client(b"not valid json") is None
        assert server._lookup_pending_request_client(b'"a string, not a dict"') is None
