"""A pre-assigned slot the printer could not be told about is retried (SA-3).

SpoolBuddy assigns a spool to an empty slot with an empty fingerprint; the
slot is configured on the printer once filament goes in. The fingerprint was
recorded even when nothing could be sent (no MQTT client while the printer
reconnects), and a recorded fingerprint ends the replay, so the slot was never
configured.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.spool_assignment import SpoolAssignment
from backend.tests.integration.test_inventory_assign import _make_mock_status, spool_factory  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _fingerprint_after_insert(db_session, printer_factory, spool_factory, apply: AsyncMock):
    from backend.app.main import on_ams_change

    printer = await printer_factory(name="H2D")
    spool = await spool_factory(slicer_filament="GFL05", material="PLA")
    assignment = SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=2, tray_id=3)
    db_session.add(assignment)
    await db_session.commit()
    ams_data = [{"id": 2, "tray": [{"id": 3, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]
    status = _make_mock_status(ams_data=ams_data)

    with (
        patch("backend.app.api.routes.inventory.apply_spool_to_slot_via_mqtt", apply),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
    ):
        pm.get_printer.return_value = MagicMock(name="H2D", serial_number="0948BB540200427")
        pm.get_status.return_value = status
        pm.get_client.return_value = MagicMock()
        pm.get_model.return_value = "H2D"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()
        await on_ams_change(printer.id, ams_data)

    await db_session.refresh(assignment)
    apply.assert_awaited_once()
    return assignment.fingerprint_type, assignment.fingerprint_color


async def test_a_slot_the_printer_could_not_be_told_about_is_retried(
    async_client, db_session, printer_factory, spool_factory
):
    """No MQTT client (reconnecting): nothing was sent."""
    apply = AsyncMock(return_value=False)

    assert await _fingerprint_after_insert(db_session, printer_factory, spool_factory, apply) == (None, None)


async def test_a_configuration_that_raised_is_not_retried(async_client, db_session, printer_factory, spool_factory):
    """It may have sent part of the commands, and would raise again on every
    AMS change, mid-print included (remain% steps count as changes)."""
    apply = AsyncMock(side_effect=RuntimeError("preset lookup failed"))

    assert await _fingerprint_after_insert(db_session, printer_factory, spool_factory, apply) == ("PLA", "FF0000FF")


async def test_a_configured_slot_records_its_fingerprint(async_client, db_session, printer_factory, spool_factory):
    apply = AsyncMock(return_value=True)

    assert await _fingerprint_after_insert(db_session, printer_factory, spool_factory, apply) == ("PLA", "FF0000FF")


async def _replay(db_session, printer_factory, spool_factory, *, state, tray_now=255, extruder_slots=None):
    """One AMS change for a pre-assigned AMS 2 slot 3 (global tray 11) that now holds filament."""
    from backend.app.main import on_ams_change

    printer = await printer_factory(name="A1 Mini")
    spool = await spool_factory(slicer_filament="GFL99", material="PLA")
    assignment = SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=2, tray_id=3)
    db_session.add(assignment)
    await db_session.commit()
    ams_data = [{"id": 2, "tray": [{"id": 3, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]
    status = _make_mock_status(ams_data=ams_data)
    status.state = state
    status.tray_now = tray_now
    status.extruder_slots = extruder_slots or {}
    apply = AsyncMock(return_value=True)

    with (
        patch("backend.app.api.routes.inventory.apply_spool_to_slot_via_mqtt", apply),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
    ):
        pm.get_printer.return_value = MagicMock(name="A1 Mini", serial_number="0309CA000000001")
        pm.get_status.return_value = status
        pm.get_client.return_value = MagicMock()
        pm.get_model.return_value = "A1 Mini"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()
        await on_ams_change(printer.id, ams_data)

    await db_session.refresh(assignment)
    return apply, assignment


class TestReplayIntoTheSlotFeedingAPrint:
    """#3434: a spool swapped in after a runout is assigned while the print runs
    from that slot. The printer refuses a setting for it then, and the replay
    sent one per AMS change for the rest of the print."""

    async def test_held_while_the_slot_feeds_the_print(self, async_client, db_session, printer_factory, spool_factory):
        apply, assignment = await _replay(db_session, printer_factory, spool_factory, state="RUNNING", tray_now=11)
        apply.assert_not_awaited()
        # Nothing recorded, so the first AMS change after the print still replays.
        assert assignment.fingerprint_type is None

    async def test_held_for_a_slot_an_extruder_is_fed_from(
        self, async_client, db_session, printer_factory, spool_factory
    ):
        """Dual nozzle: tray_now names the other hotend's slot."""
        from backend.app.services.bambu_mqtt import ExtruderSlot

        slots = {0: ExtruderSlot(ams_id=0, slot_id=0, has_filament=True), 1: ExtruderSlot(2, 3, True)}
        apply, _ = await _replay(
            db_session, printer_factory, spool_factory, state="RUNNING", tray_now=0, extruder_slots=slots
        )
        apply.assert_not_awaited()

    async def test_another_slot_is_configured_mid_print(self, async_client, db_session, printer_factory, spool_factory):
        apply, assignment = await _replay(db_session, printer_factory, spool_factory, state="RUNNING", tray_now=0)
        apply.assert_awaited_once()
        assert assignment.fingerprint_type == "PLA"

    async def test_configured_once_the_print_is_over(self, async_client, db_session, printer_factory, spool_factory):
        apply, assignment = await _replay(db_session, printer_factory, spool_factory, state="FINISH", tray_now=11)
        apply.assert_awaited_once()
        assert assignment.fingerprint_type == "PLA"
