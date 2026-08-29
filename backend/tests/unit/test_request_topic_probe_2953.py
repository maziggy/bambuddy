"""The request-topic probe must not latch on a single unexplained drop (#2953).

Bambuddy subscribes to the printer's own request topic to intercept the
``ams_mapping`` a slicer sends with a print. A1-class printers refuse: their
broker kills the TCP connection instead of returning a SUBACK failure, so the
only signal is "we subscribed and then got disconnected". That signal is
circumstantial. Every other reason a connection can drop inside the same few
seconds -- a network blip, the printer rebooting, the container being stopped
mid-probe -- looks exactly the same.

Latching on the first one costs ams_mapping capture for the rest of the
process on a printer that supports the topic perfectly well, and every Studio
print after that is charged to a spool picked by tray position. Requiring the
drop to repeat costs a printer that genuinely refuses one extra reconnect.
"""

from types import SimpleNamespace

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture(autouse=True)
def _clear_class_state():
    BambuMQTTClient._request_topic_cache.clear()
    BambuMQTTClient._request_topic_probe_failures.clear()
    yield
    BambuMQTTClient._request_topic_cache.clear()
    BambuMQTTClient._request_topic_probe_failures.clear()


def _client(serial="SER2953"):
    client = BambuMQTTClient(ip_address="10.0.0.9", serial_number=serial, access_code="12345678")
    client._stale_reconnecting = False
    client._last_message_time = 0.0
    client.last_connect_error = None
    return client


def _mid_probe(client):
    """Put the client where it is right after subscribing to the request topic."""
    import time

    client._request_topic_sub_mid = 7
    client._request_topic_sub_time = time.time()
    client._request_topic_confirmed = False


def _drop(client):
    client._on_disconnect(None, None, disconnect_flags=None, rc=SimpleNamespace(is_failure=True))


def test_one_drop_keeps_the_request_topic_enabled():
    """A blip, as far as we can tell. Try again on the next connection."""
    client = _client()
    _mid_probe(client)

    _drop(client)

    assert client._request_topic_supported is True
    assert BambuMQTTClient._request_topic_cache.get("SER2953") is None
    assert BambuMQTTClient._request_topic_probe_failures["SER2953"] == 1


def test_a_second_drop_disables_it():
    """The A1's answer: same response every time. Stop asking, and stop
    causing a reconnect on every connection."""
    client = _client()
    _mid_probe(client)
    _drop(client)
    _mid_probe(client)

    _drop(client)

    assert client._request_topic_supported is False
    assert BambuMQTTClient._request_topic_cache["SER2953"] is False


def test_a_new_client_for_a_disabled_printer_does_not_re_probe():
    """Unchanged: once disabled, later instances skip the subscription
    entirely rather than reopening the reconnect loop."""
    client = _client()
    _mid_probe(client)
    _drop(client)
    _mid_probe(client)
    _drop(client)

    assert _client()._request_topic_supported is False


def test_a_successful_suback_clears_the_count():
    """A printer that answered once has answered. A later isolated drop must
    start from zero, not from a half-spent budget."""
    client = _client()
    _mid_probe(client)
    _drop(client)
    assert BambuMQTTClient._request_topic_probe_failures["SER2953"] == 1

    client._request_topic_sub_mid = 7
    client._on_subscribe(None, None, 7, [SimpleNamespace(is_failure=False, value=0, getName=lambda: "ok")])

    assert BambuMQTTClient._request_topic_cache["SER2953"] is True
    assert "SER2953" not in BambuMQTTClient._request_topic_probe_failures


def test_a_suback_rejection_still_disables_immediately():
    """A SUBACK failure is the broker answering the question, not evidence
    about it. One is enough."""
    client = _client()
    client._request_topic_sub_mid = 7

    client._on_subscribe(None, None, 7, [SimpleNamespace(is_failure=True, value=135, getName=lambda: "Not authorized")])

    assert client._request_topic_supported is False
    assert BambuMQTTClient._request_topic_cache["SER2953"] is False


def test_a_disconnect_we_asked_for_is_not_evidence():
    """Shutting the container down mid-probe used to count against the
    printer. ``disconnect()`` sets the event before closing the socket."""
    import threading

    client = _client()
    _mid_probe(client)
    client._disconnection_event = threading.Event()

    _drop(client)

    assert client._request_topic_supported is True
    assert "SER2953" not in BambuMQTTClient._request_topic_probe_failures
