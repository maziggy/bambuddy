"""The MQTT smart plug service must never run more than one paho client (#3747).

``configure()`` calls ``_connect()`` whenever the service isn't connected, and
every settings save that reconfigures the relay calls ``configure()``. While
the broker was unreachable or the credentials were wrong, ``_connect()``
replaced ``self.client`` without retiring the client already there, whose
network thread kept auto-reconnecting. ``disconnect()`` only retires the
current client, so the earlier ones ran until the process ended. All of them
use the same client ID, so once the broker accepted them they took each
other's session over (MQTT 3.1.1, section 3.1.4) in an endless loop of
"connected successfully" and "disconnected: Unspecified error", several times
per millisecond. On the reporter's install that grew the service's stderr log
to 9.1 GB in two days.
"""

import asyncio
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services import mqtt_smart_plug as module
from backend.app.services.mqtt_smart_plug import MQTTSmartPlugService

SETTINGS = {"mqtt_enabled": True, "mqtt_broker": "127.0.0.1", "mqtt_port": 1883}


async def _no_sleep(_seconds):
    """Skips _connect()'s one-second wait for the connection."""
    return None


class _FakePahoFactory:
    """Stands in for ``mqtt.Client``: hands out mocks whose connection never
    comes up, and remembers them in order."""

    def __init__(self):
        self.created: list[MagicMock] = []

    def __call__(self, *_args, **_kwargs):
        client = MagicMock(name=f"paho.Client#{len(self.created)}")
        self.created.append(client)
        return client


@pytest.fixture
def fake_paho():
    factory = _FakePahoFactory()
    with (
        patch.object(module.mqtt, "Client", side_effect=factory),
        patch.object(module, "retire_paho_client") as retire,
        patch.object(module.asyncio, "sleep", new=_no_sleep),
    ):
        yield factory, retire


def _retired(retire: MagicMock) -> list:
    return [c.args[0] for c in retire.call_args_list]


class TestOneClientAtATime:
    @pytest.mark.asyncio
    async def test_reconfiguring_while_disconnected_retires_the_previous_client(self, fake_paho):
        factory, retire = fake_paho
        service = MQTTSmartPlugService()

        # Three settings saves while the broker can't be reached.
        for _ in range(3):
            await service.configure(SETTINGS)

        first, second, third = factory.created
        assert [c.args for c in retire.call_args_list] == [(first, "smart-plugs"), (second, "smart-plugs")]
        assert service.client is third

    @pytest.mark.asyncio
    async def test_a_connected_client_is_kept(self, fake_paho):
        factory, retire = fake_paho
        service = MQTTSmartPlugService()

        await service.configure(SETTINGS)
        service.connected = True  # what _on_connect sets once the broker accepts it
        await service.configure(SETTINGS)

        assert len(factory.created) == 1
        retire.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_after_repeated_saves_has_retired_every_client(self, fake_paho):
        factory, retire = fake_paho
        service = MQTTSmartPlugService()

        await service.configure(SETTINGS)
        await service.configure(SETTINGS)
        await service.disconnect()

        assert _retired(retire) == factory.created
        assert service.client is None
        assert service.connected is False


def _closed_port() -> int:
    """A local TCP port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _paho_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if t.name.startswith("paho-mqtt-client")}


async def _new_paho_threads(before: set[threading.Thread], at_most: int) -> int:
    """Paho threads not in *before*, once at most *at_most* are left.

    Retired clients shut down on threads of their own, so give them up to
    five seconds to finish before counting.
    """
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(_paho_threads() - before) > at_most:
        await asyncio.sleep(0.05)
    return len(_paho_threads() - before)


@pytest.mark.asyncio
async def test_no_paho_network_thread_outlives_disconnect():
    """With real paho: three saves against an unreachable broker left two
    auto-reconnecting network threads running after disconnect()."""
    before = _paho_threads()
    service = MQTTSmartPlugService()
    settings = {**SETTINGS, "mqtt_port": _closed_port()}

    with patch.object(module.asyncio, "sleep", new=_no_sleep):
        for _ in range(3):
            await service.configure(settings)
    assert await _new_paho_threads(before, at_most=1) <= 1  # only the current client's loop

    await service.disconnect()

    assert await _new_paho_threads(before, at_most=0) == 0
