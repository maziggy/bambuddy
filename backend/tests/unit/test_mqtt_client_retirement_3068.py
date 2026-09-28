"""Letting go of a paho client must never block the thread that let it go.

#3068: a printer that had been offline 38 hours still answered on 8883. The
connection watchdog rebuilt its session, which ended in paho's `loop_stop()`
-- set a terminate flag, then `join()` the network thread with no timeout. The
network thread was parked in `reconnect()`'s TLS handshake, where it cannot
read that flag, so the join never returned. The join was running on the asyncio
thread: the process stayed up, `/health` stopped being answered, and Docker's
`restart: unless-stopped` never fired because nothing had exited.

Four call paths reach that join from the event loop -- the connection watchdog,
the queue dispatch deadline, `check_staleness()` on an ordinary status poll,
and `disconnect()` from the printer routes. They all funnel through the two
places tested here. The relay and smart-plug services had the same join on
their shutdown path and now share the same teardown.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.utils.paho_teardown import retire_paho_client


class WedgedPahoClient:
    """A paho client whose network thread will not stop.

    `loop_stop()` blocks until `release()` is called, which is what a real one
    does while its thread sits in `do_handshake()` against a printer that
    answers TCP and then goes quiet.
    """

    def __init__(self):
        self.released = threading.Event()
        self.disconnect_called = threading.Event()
        self.loop_stop_returned = threading.Event()
        self.on_connect = "sentinel"
        self.on_disconnect = "sentinel"
        self.on_subscribe = "sentinel"
        self.on_message = "sentinel"

    def disconnect(self):
        self.disconnect_called.set()

    def loop_stop(self):
        self.released.wait(timeout=10)
        self.loop_stop_returned.set()

    def release(self):
        self.released.set()


@pytest.fixture
def client():
    return BambuMQTTClient(
        ip_address="192.168.1.100",
        serial_number="00M09A123456789",
        access_code="12345678",
    )


class TestRetiringAClient:
    def test_it_returns_while_the_old_client_is_still_stopping(self):
        wedged = WedgedPahoClient()
        try:
            started = time.monotonic()
            retire_paho_client(wedged, "00M09A123456789")
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"retirement blocked the caller for {elapsed:.2f}s (#3068)"
            assert not wedged.loop_stop_returned.is_set(), "loop_stop was joined, not handed off"
        finally:
            wedged.release()

    def test_the_callbacks_are_detached_before_the_caller_moves_on(self):
        # Blocking until the network thread was gone is what used to guarantee
        # a client we had let go of could no longer touch our state. With the
        # teardown detached, a zombie that finishes its handshake would
        # auto-reconnect and set connected=True behind its replacement's back,
        # so the detach has to happen inline.
        wedged = WedgedPahoClient()
        try:
            retire_paho_client(wedged, "00M09A123456789")
            assert wedged.on_connect is None
            assert wedged.on_disconnect is None
            assert wedged.on_subscribe is None
            assert wedged.on_message is None
        finally:
            wedged.release()

    def test_the_old_session_is_still_disconnected_and_stopped(self):
        # disconnect() is what stops paho's auto-reconnect, and with it the
        # chance of an unacked project_file replaying onto a revived session
        # (#1136). Handing it off must not mean skipping it.
        wedged = WedgedPahoClient()
        assert wedged.disconnect_called.wait(timeout=0) is False
        retire_paho_client(wedged, "00M09A123456789")
        assert wedged.disconnect_called.wait(timeout=5), "the old client was never disconnected"
        wedged.release()
        assert wedged.loop_stop_returned.wait(timeout=5), "the old client's loop was never stopped"

    def test_a_client_that_raises_on_teardown_is_still_let_go(self):
        exploding = MagicMock()
        exploding.disconnect.side_effect = RuntimeError("socket already gone")
        exploding.loop_stop.side_effect = RuntimeError("no thread")

        retire_paho_client(exploding, "00M09A123456789")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not exploding.loop_stop.called:
            time.sleep(0.01)
        assert exploding.loop_stop.called

    def test_the_retirement_thread_is_named_for_the_printer(self):
        # A support bundle's thread dump is how the next one of these gets
        # recognised; an anonymous Thread-7 says nothing.
        wedged = WedgedPahoClient()
        try:
            retire_paho_client(wedged, "00M09A123456789")
            names = [t.name for t in threading.enumerate()]
            assert "mqtt-retire-00M09A123456789" in names
        finally:
            wedged.release()


class TestHardReset:
    def test_it_does_not_wait_for_the_old_network_thread(self, client):
        wedged = WedgedPahoClient()
        client._client = wedged
        client._loop = None  # no rebuild, so only the teardown is measured

        try:
            started = time.monotonic()
            client._hard_reset_client()
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"_hard_reset_client blocked for {elapsed:.2f}s (#3068)"
            assert client._client is None
        finally:
            wedged.release()

    def test_the_replacement_gets_a_fresh_client_id(self, client):
        # The #1136 property: the new session must not inherit paho's QoS 1
        # queue, which is what a new client_id buys.
        wedged = WedgedPahoClient()
        client._client = wedged
        client._loop = MagicMock()

        with patch("backend.app.services.bambu_mqtt.mqtt.Client") as MockClient:
            MockClient.return_value = MagicMock()
            try:
                client._hard_reset_client()
            finally:
                wedged.release()

            assert MockClient.call_count == 1
            new_id = MockClient.call_args.kwargs["client_id"]
            assert client.serial_number in new_id
            assert client._client is MockClient.return_value

    @pytest.mark.asyncio
    async def test_a_wedged_printer_does_not_stall_the_event_loop(self, client):
        # The reported failure, end to end: force_reconnect_stale_session is
        # what the connection watchdog and the queue dispatch deadline both
        # call, from a coroutine. A heartbeat has to keep ticking through it.
        wedged = WedgedPahoClient()
        client._client = wedged

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            with patch("backend.app.services.bambu_mqtt.mqtt.Client") as MockClient:
                MockClient.return_value = MagicMock()
                started = time.monotonic()
                client.force_reconnect_stale_session("offline for 900s, port still answering")
                elapsed = time.monotonic() - started
            await asyncio.sleep(0.1)
        finally:
            beat.cancel()
            wedged.release()
            try:
                await beat
            except asyncio.CancelledError:
                pass

        assert elapsed < 2.0, f"the forced reconnect held the event loop for {elapsed:.2f}s (#3068)"
        assert ticks > 0, "the event loop made no progress while the old client was stopping"
        assert client.state.connected is False


class TestDisconnect:
    def test_it_does_not_wait_for_the_old_network_thread(self, client):
        # Reached from PUT/DELETE /printers/{id} and POST
        # /printers/{id}/disconnect, all on the asyncio thread.
        wedged = WedgedPahoClient()
        client._client = wedged
        client.state.connected = True

        try:
            started = time.monotonic()
            client.disconnect()
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"disconnect() blocked for {elapsed:.2f}s (#3068)"
            assert client._client is None
            assert client.state.connected is False
        finally:
            wedged.release()

    def test_the_disconnect_callback_still_gets_its_window(self, client):
        # The callback that releases the timeout fires on paho's thread, so it
        # has to run before the retirement detaches it -- otherwise every
        # caller with a non-zero timeout waits the timeout out in full.
        class AnsweringClient(WedgedPahoClient):
            def disconnect(self):
                super().disconnect()
                if self.on_disconnect is not None:
                    self.on_disconnect(self, None)

        answering = AnsweringClient()
        answering.on_disconnect = client._on_disconnect  # as connect() wires it
        client._client = answering

        try:
            started = time.monotonic()
            client.disconnect(timeout=5)
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, (
                f"disconnect(timeout=5) took {elapsed:.2f}s — the callback was detached "
                "before it could report the disconnect"
            )
            assert client._disconnection_event.is_set()
        finally:
            answering.release()

    def test_disconnecting_twice_is_harmless(self, client):
        wedged = WedgedPahoClient()
        client._client = wedged
        try:
            client.disconnect()
            client.disconnect()
            assert client._client is None
        finally:
            wedged.release()


class TestTheOtherMqttServices:
    """The relay and the smart-plug service tear their brokers down the same
    way, at shutdown. A wedged broker there does not stop request serving --
    nothing is being served by then -- but it does stop the process exiting,
    which leaves the container to be killed rather than stopped."""

    @pytest.mark.asyncio
    async def test_the_relay_does_not_wait_for_its_network_thread(self):
        from backend.app.services.mqtt_relay import MQTTRelayService

        wedged = WedgedPahoClient()
        service = MQTTRelayService()
        service.client = wedged
        service.connected = True

        try:
            started = time.monotonic()
            await service.disconnect()
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"relay shutdown blocked for {elapsed:.2f}s (#3068)"
            assert service.client is None
            # Only the retirement detaches callbacks, so this proves it ran
            # rather than the service's except-block swallowing it.
            assert wedged.on_disconnect is None
        finally:
            wedged.release()

    @pytest.mark.asyncio
    async def test_the_smart_plug_service_does_not_wait_for_its_network_thread(self):
        from backend.app.services.mqtt_smart_plug import MQTTSmartPlugService

        wedged = WedgedPahoClient()
        service = MQTTSmartPlugService()
        service.client = wedged
        service.connected = True

        try:
            started = time.monotonic()
            await service.disconnect()
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"smart-plug shutdown blocked for {elapsed:.2f}s (#3068)"
            assert service.client is None
            # Only the retirement detaches callbacks, so this proves it ran
            # rather than the service's except-block swallowing it.
            assert wedged.on_disconnect is None
        finally:
            wedged.release()


class TestDisconnectStaysQuiet:
    def test_a_hand_disconnected_printer_is_not_announced_as_offline(self, client):
        # paho's disconnect callback used to land during the join, but
        # `_on_disconnect` suppresses itself for a clean disconnect of a
        # printer that reported in the last 10s, so a healthy printer
        # disconnected on purpose never broadcast one. Announcing it here
        # instead would reach the connected→disconnected edge and notify the
        # user their printer went offline a minute later (#1752).
        seen = []
        client.on_state_change = seen.append
        client._last_message_time = time.time()
        wedged = WedgedPahoClient()
        client._client = wedged
        client.state.connected = True

        try:
            client.disconnect()
        finally:
            wedged.release()

        assert seen == [], "disconnecting a printer by hand announced it as offline"
        assert client.state.connected is False
