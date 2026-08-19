"""Dropping a printer's client must not forget what filament it had (#2876).

The queue's smart-plug step reads a printer's last known trays to decide
which machine is worth switching on for a job. It gets there through
``_power_on_and_wait``, which calls ``connect_printer`` in a retry loop --
and that replaces the client object, taking its status with it. Before
this, every attempt to wake a printer that did not come back erased the
reading the next attempt depends on, so a farm that had been power-cycled
a few times knew nothing about itself and went back to switching machines
on one at a time.

The record is kept beside the clients rather than inside one: it answers
"what did this printer last have loaded", which is not the same question
as "what is it reporting now", and nothing that displays or merges live
status may confuse the two.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_manager import PrinterManager

AMS = [{"id": 0, "tray": [{"tray_type": "PLA", "tray_color": "161616FF"}]}]
VT_TRAY = [{"id": 254, "tray_type": "ASA", "tray_color": "F98C36FF"}]


@pytest.fixture
def manager():
    return PrinterManager()


def _printer():
    return SimpleNamespace(
        id=1,
        name="X1C-1",
        ip_address="10.0.0.1",
        access_code="x",
        serial_number="X1C0001",
        model="X1C",
    )


def _seed(manager, raw_data):
    """Put a client holding ``raw_data`` where the manager will replace it."""
    client = MagicMock()
    client.state = PrinterState()
    client.state.raw_data = raw_data
    manager._clients[1] = client
    return client


async def _connect(manager):
    """Run ``connect_printer`` against a fake MQTT client, return that client."""
    created = MagicMock()
    created.state = PrinterState()
    with patch("backend.app.services.printer_manager.BambuMQTTClient", return_value=created):
        await manager.connect_printer(_printer())
    return created


@pytest.mark.asyncio
async def test_the_loaded_filament_survives_a_reconnect(manager):
    _seed(manager, {"ams": AMS, "vt_tray": VT_TRAY})

    await _connect(manager)

    assert manager.last_known_trays(1) == {"ams": AMS, "vt_tray": VT_TRAY}


def test_the_loaded_filament_survives_a_plain_disconnect(manager):
    _seed(manager, {"ams": AMS})

    manager.disconnect_printer(1)

    assert manager.last_known_trays(1) == {"ams": AMS}


@pytest.mark.asyncio
async def test_the_new_client_starts_empty(manager):
    """The record is history, not status.

    Writing it into the reconnecting client's ``raw_data`` would put it in
    front of everything that reads live status -- and the AMS merge is
    additive, so an AMS unit unplugged while the printer was off would be
    merged straight back in and never leave again.
    """
    _seed(manager, {"ams": AMS, "vt_tray": VT_TRAY})

    created = await _connect(manager)

    assert created.state.raw_data == {}


@pytest.mark.asyncio
async def test_nothing_but_the_trays_is_kept(manager):
    """A stale temperature or print state read as current would be worse
    than nothing; what is physically loaded survives a power cycle."""
    _seed(manager, {"ams": AMS, "bed_temper": 60.0, "gcode_state": "RUNNING", "layer_num": 42})

    await _connect(manager)

    assert manager.last_known_trays(1) == {"ams": AMS}


@pytest.mark.asyncio
async def test_a_first_connection_has_nothing_to_remember(manager):
    await _connect(manager)

    assert manager.last_known_trays(1) == {}


@pytest.mark.asyncio
async def test_an_empty_reading_is_not_remembered_as_a_reading(manager):
    """A printer that reported no trays leaves no trays behind.

    The queue reads an absent record as "we have never heard" and switches
    the printer on anyway; storing empty lists would say the same thing in
    a shape that is harder to tell from a real answer.
    """
    _seed(manager, {"ams": [], "vt_tray": []})

    await _connect(manager)

    assert manager.last_known_trays(1) == {}


@pytest.mark.asyncio
async def test_a_later_reading_replaces_the_remembered_one(manager):
    _seed(manager, {"vt_tray": VT_TRAY})
    await _connect(manager)

    _seed(manager, {"vt_tray": [{"id": 254, "tray_type": "PETG", "tray_color": "00FF00FF"}]})
    await _connect(manager)

    assert manager.last_known_trays(1)["vt_tray"][0]["tray_type"] == "PETG"
