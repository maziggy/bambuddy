"""Personal access codes at a virtual printer's FTP and MQTT sign-in (#1430).

The VP's own code signs in as before. Any other code is handed to the
instance's lookup; a user id back means it is that user's personal code, and
the FTP upload is passed on as theirs. Wrong codes count towards a per-IP
limit on FTP, as on MQTT.
"""

import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.virtual_printer.ftp_server import AuthRateLimiter, FTPSession
from backend.tests.unit.test_vp_mqtt_server import _build_connect_payload, _make_server


def _writer():
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.get_extra_info = MagicMock(return_value=("192.168.1.99", 12345))
    return writer


def _session(tmp_path, *, authenticate_user=None, rate_limiter=None, on_file_received=None) -> FTPSession:
    session = FTPSession(
        reader=asyncio.StreamReader(),
        writer=_writer(),
        upload_dir=tmp_path,
        access_code="12345678",
        ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER),
        on_file_received=on_file_received,
        bind_address="127.0.0.1",
        vp_name="codes-test",
        authenticate_user=authenticate_user,
        rate_limiter=rate_limiter,
    )
    session.username = "bblp"
    return session


def _replies(session) -> list[str]:
    return [call.args[0].decode() for call in session.writer.write.call_args_list]


class TestFtpSignIn:
    @pytest.mark.asyncio
    async def test_the_vp_code_signs_in_without_a_user(self, tmp_path):
        lookup = AsyncMock(return_value=7)
        session = _session(tmp_path, authenticate_user=lookup)

        await session.cmd_PASS("12345678")

        assert session.authenticated and session.user_id is None
        lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_personal_code_signs_in_as_its_user(self, tmp_path):
        lookup = AsyncMock(return_value=7)
        session = _session(tmp_path, authenticate_user=lookup)

        await session.cmd_PASS("87654321")

        lookup.assert_awaited_once_with("87654321")
        assert session.authenticated and session.user_id == 7
        assert _replies(session)[-1].startswith("230")

    @pytest.mark.asyncio
    async def test_a_code_nobody_has_is_refused(self, tmp_path):
        session = _session(tmp_path, authenticate_user=AsyncMock(return_value=None))

        await session.cmd_PASS("87654321")

        assert not session.authenticated
        assert _replies(session)[-1].startswith("530")

    @pytest.mark.parametrize(("user_id", "expected_kwargs"), [(7, {"user_id": 7}), (None, {})])
    @pytest.mark.asyncio
    async def test_the_upload_is_passed_on_as_the_users(self, tmp_path, user_id, expected_kwargs):
        from backend.tests.unit.test_vp_ftp_stor import _make_session, _valid_3mf_bytes

        session = _make_session(tmp_path, data_chunks=[_valid_3mf_bytes()])
        session.send = AsyncMock()
        session.user_id = user_id
        session.on_file_received = AsyncMock()

        await session.cmd_STOR("plate.gcode.3mf")

        args, kwargs = session.on_file_received.call_args
        assert args == (session.upload_dir / "plate.gcode.3mf", "192.168.1.99")
        assert kwargs == expected_kwargs

    @pytest.mark.asyncio
    async def test_five_wrong_codes_lock_the_address_out_even_for_the_right_one(self, tmp_path):
        limiter = AuthRateLimiter(max_attempts=5, window_seconds=60)
        for _ in range(5):
            await _session(tmp_path, rate_limiter=limiter).cmd_PASS("00000000")

        locked = _session(tmp_path, rate_limiter=limiter, authenticate_user=AsyncMock(return_value=7))
        await locked.cmd_PASS("12345678")

        assert not locked.authenticated
        locked.authenticate_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_good_sign_in_does_not_wipe_earlier_failures(self, tmp_path):
        # Four wrong codes, one good one, one wrong: the IP is out. Wiping on
        # success let a user guess others' codes between their own sign-ins.
        limiter = AuthRateLimiter(max_attempts=5, window_seconds=60)
        for _ in range(4):
            await _session(tmp_path, rate_limiter=limiter).cmd_PASS("00000000")
        good = _session(tmp_path, rate_limiter=limiter)
        await good.cmd_PASS("12345678")
        assert good.authenticated
        await _session(tmp_path, rate_limiter=limiter).cmd_PASS("00000000")

        assert limiter.is_limited("192.168.1.99")

    @pytest.mark.asyncio
    async def test_no_second_sign_in_on_a_session_already_in(self, tmp_path):
        lookup = AsyncMock(return_value=7)
        session = _session(tmp_path, authenticate_user=lookup)
        await session.cmd_PASS("12345678")

        await session.cmd_PASS("87654321")

        lookup.assert_not_awaited()
        assert session.user_id is None
        assert _replies(session)[-1].startswith("503")

    @pytest.mark.asyncio
    async def test_parallel_sign_ins_are_counted_before_the_lookup(self, tmp_path):
        # The lookup awaits the database; attempts still in flight must count,
        # or many parallel connections all pass the limit check first.
        limiter = AuthRateLimiter(max_attempts=5, window_seconds=60)
        gate = asyncio.Event()
        lookups = 0

        async def slow_lookup(_code):
            nonlocal lookups
            lookups += 1
            await gate.wait()
            return None

        sessions = [_session(tmp_path, rate_limiter=limiter, authenticate_user=slow_lookup) for _ in range(8)]
        tasks = [asyncio.create_task(s.cmd_PASS("87654321")) for s in sessions]
        await asyncio.sleep(0.01)
        gate.set()
        await asyncio.gather(*tasks)

        assert lookups == 5
        assert all(not s.authenticated for s in sessions)


class TestMqttSignIn:
    def test_a_personal_code_signs_in(self):
        server = _make_server()
        server.authenticate_user = AsyncMock(return_value=7)

        result = asyncio.run(server._handle_connect(_build_connect_payload(60, access_code="87654321"), _writer()))

        assert result == (True, 60)
        server.authenticate_user.assert_awaited_once_with("87654321")

    def test_a_code_nobody_has_is_refused(self):
        server = _make_server()
        server.authenticate_user = AsyncMock(return_value=None)

        result = asyncio.run(server._handle_connect(_build_connect_payload(60, access_code="87654321"), _writer()))

        assert result == (False, 0)

    def test_only_for_the_bblp_user(self):
        server = _make_server()
        server.authenticate_user = AsyncMock(return_value=7)

        result = asyncio.run(
            server._handle_connect(_build_connect_payload(60, access_code="87654321", username="root"), _writer())
        )

        assert result == (False, 0)
        server.authenticate_user.assert_not_awaited()


class TestMqttBridgeCommands:
    """A personal code lets someone send files. Commands the bridge would pass
    to the real printer (stop, pause, G-code, AMS) go only with printers:control,
    asked fresh for every command; the VP's own code is unchanged."""

    def _publish(self, server, writer, command=None):
        from backend.tests.unit.test_vp_mqtt_server import _build_publish_payload

        server._client_serials["c1"] = server.serial
        payload = _build_publish_payload(
            "device/01P00AABCDEFGHI/request", {"print": command or {"command": "stop", "sequence_id": "1"}}
        )
        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

    def _server(self, authorize_control):
        server = _make_server()
        server._bridge = MagicMock(is_active=True)
        server.authorize_control = authorize_control
        return server

    def test_a_personal_code_without_control_reaches_no_printer(self):
        server = self._server(AsyncMock(return_value=False))
        writer = _writer()
        server._connection_users[writer] = 7

        self._publish(server, writer)

        server.authorize_control.assert_awaited_once_with(7)
        server._bridge.forward_to_printer.assert_not_called()

    def test_with_control_it_does(self):
        server = self._server(AsyncMock(return_value=True))
        writer = _writer()
        server._connection_users[writer] = 7

        self._publish(server, writer)

        server._bridge.forward_to_printer.assert_called_once()

    def test_the_vp_code_needs_no_check(self):
        server = self._server(AsyncMock(return_value=False))

        self._publish(server, _writer())

        server.authorize_control.assert_not_awaited()
        server._bridge.forward_to_printer.assert_called_once()

    def test_the_personal_sign_in_is_remembered_for_the_connection(self):
        server = _make_server()
        server.authenticate_user = AsyncMock(return_value=7)
        writer = _writer()

        asyncio.run(server._handle_connect(_build_connect_payload(60, access_code="87654321"), writer))

        assert server._connection_users[writer] == 7
