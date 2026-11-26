"""The smart-plug service must never run more than one paho client.

#3747: every settings save that reconfigures the relay also calls
`MQTTSmartPlugService.configure()`, which calls `_connect()` whenever the
service isn't connected. `_connect()` built a fresh client and dropped the old
one on the floor -- but a client whose connect failed (broker not up yet, wrong
credentials) is still running `loop_start()`'s thread and auto-reconnecting.
Once the broker accepted them, the leaked clients all shared one client ID, so
the broker kept disconnecting each in favour of the next: an endless
connected/disconnected loop that wrote 9.1 GB of log in two days.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import mqtt_smart_plug
from backend.app.services.mqtt_smart_plug import MQTTSmartPlugService
from backend.app.utils.paho_teardown import retire_paho_client

SETTINGS = {
    "mqtt_enabled": True,
    "mqtt_broker": "broker.local",
    "mqtt_port": 1883,
    "mqtt_username": "bambuddy",
    "mqtt_password": "wrong",
    "mqtt_use_tls": False,
}


@pytest.mark.asyncio
async def test_a_reconfigure_while_disconnected_retires_the_old_client():
    # The mocked clients never call on_connect, which is the broker that is
    # down or refusing the credentials: the service stays disconnected, so
    # every configure() goes back through _connect().
    clients = []

    def make_client(*args, **kwargs):
        client = MagicMock(name=f"paho-client-{len(clients)}")
        clients.append(client)
        return client

    service = MQTTSmartPlugService()
    with (
        patch.object(mqtt_smart_plug.mqtt, "Client", side_effect=make_client),
        patch.object(mqtt_smart_plug, "retire_paho_client", wraps=retire_paho_client) as retire,
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        for _ in range(3):
            assert await service.configure(SETTINGS) is True

        assert len(clients) == 3
        retired = [c.args[0] for c in retire.call_args_list]
        assert retired == clients[:2], "a replaced client was left running (#3747)"
        assert service.client is clients[2]
        # Retirement detaches the callbacks inline, so a leaked client that
        # does get through to the broker can no longer flip our state.
        for old in clients[:2]:
            assert old.on_connect is None
            assert old.on_disconnect is None

        await service.disconnect()

        retired = [c.args[0] for c in retire.call_args_list]
        assert retired == clients, "disconnect() left an earlier client running (#3747)"
        assert service.client is None
