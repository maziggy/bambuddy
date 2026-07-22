"""End-to-end regression test for the #2629 queue stall.

Exercises the real objects rather than mocks: a real ``BambuMQTTClient``
registered on the real ``printer_manager`` singleton, driven through the real
``_on_message`` path, and read back through the scheduler's own idle check.
That chain — presume power off, printer keeps talking, scheduler sees it as
dispatchable again — is what actually broke for the reporter, and no single
unit test covers it.
"""

from __future__ import annotations

import json

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.printer_manager import printer_manager

PRINTER_ID = 9629  # unlikely to collide with any other test's registrations


class _Msg:
    def __init__(self, topic: str, data: dict):
        self.topic = topic
        self.payload = json.dumps(data).encode()


@pytest.fixture
def registered_client():
    """A connected client sitting on FINISH, as after a completed print."""
    client = BambuMQTTClient(ip_address="10.0.0.5", serial_number="SER2629", access_code="12345678")
    client.state.connected = True
    client.state.state = "FINISH"
    printer_manager._clients[PRINTER_ID] = client
    try:
        yield client
    finally:
        printer_manager._clients.pop(PRINTER_ID, None)


def _partial_push(client: BambuMQTTClient) -> None:
    """A steady-state push_status carrying no gcode_state — the frame shape the
    reporter's P1S sends between state transitions."""
    client._on_message(None, None, _Msg(client.topic_subscribe, {"print": {"wifi_signal": "-30dBm"}}))


def test_printer_recovers_and_queue_can_dispatch_again(registered_client):
    scheduler = PrintScheduler()
    assert scheduler._is_printer_idle(PRINTER_ID, require_plate_clear=False) is True

    # An accessory plug (filter fan) switches off; Bambuddy presumes power loss.
    printer_manager.mark_printer_offline(PRINTER_ID)
    assert printer_manager.get_status(PRINTER_ID).state == "unknown"
    assert scheduler._is_printer_idle(PRINTER_ID, require_plate_clear=False) is False

    # The printer never stopped talking.
    _partial_push(registered_client)

    assert printer_manager.get_status(PRINTER_ID).state == "FINISH"
    assert printer_manager.is_connected(PRINTER_ID) is True
    assert scheduler._is_printer_idle(PRINTER_ID, require_plate_clear=False) is True


def test_real_power_cut_still_leaves_printer_unavailable(registered_client):
    """The recovery must key off actual traffic, not off time passing — a plug
    that really cut power produces silence, and the printer stays offline."""
    scheduler = PrintScheduler()

    printer_manager.mark_printer_offline(PRINTER_ID)

    # No messages arrive at all.
    assert printer_manager.get_status(PRINTER_ID).state == "unknown"
    assert printer_manager.is_connected(PRINTER_ID) is False
    assert scheduler._is_printer_idle(PRINTER_ID, require_plate_clear=False) is False
