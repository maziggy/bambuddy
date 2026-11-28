"""Assigning a spool on a printer that is offline (#3771).

A switched-off printer still has an MQTT client, which refuses every publish
("not connected"). The assign path ignored that, answered ``configured: true``
and kept the fingerprint of the printer's last known state, so the replay that
configures a pending slot never picked the assignment up, and a reconnect with
unchanged AMS data fired no AMS change to replay it on anyway.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.tests.integration.test_inventory_assign import _make_mock_status, spool_factory  # noqa: F401
from backend.tests.integration.test_spoolman_slot_assignment_mqtt import (  # noqa: F401
    mock_spoolman_client,
    slot_settings,
    test_printer,
)

LOADED = [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "tray_info_idx": "GFL99"}]}]


def _client(connected: bool) -> MagicMock:
    client = MagicMock()
    client.ams_set_filament_setting.return_value = connected
    client.extrusion_cali_sel.return_value = connected
    return client


async def _assign(async_client, printer_id, spool_id, client):
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_mock_status(ams_data=LOADED)
        return await async_client.post(
            "/api/v1/inventory/assignments",
            json={"spool_id": spool_id, "printer_id": printer_id, "ams_id": 0, "tray_id": 0},
        )


@pytest.mark.asyncio
@pytest.mark.integration
class TestTheAssign:
    async def test_an_offline_printer_is_pending_not_configured(
        self, async_client: AsyncClient, db_session, printer_factory, spool_factory
    ):
        printer = await printer_factory()
        spool = await spool_factory(slicer_filament="GFL99", material="PLA")
        client = _client(connected=False)

        response = await _assign(async_client, printer.id, spool.id, client)

        assert response.status_code == 200
        body = response.json()
        assert (body["configured"], body["pending_config"], body["printer_offline"]) == (False, True, True)
        # Nothing after the refused setting: no K-profile command, no read-back.
        client.extrusion_cali_sel.assert_not_called()
        client.register_assignment_verification.assert_not_called()
        # No fingerprint, so the replay configures the slot once the printer is back.
        row = await db_session.get(SpoolAssignment, body["id"])
        await db_session.refresh(row)
        assert (row.fingerprint_type, row.fingerprint_color) == (None, None)

    async def test_a_connected_printer_is_configured(
        self, async_client: AsyncClient, db_session, printer_factory, spool_factory
    ):
        printer = await printer_factory()
        spool = await spool_factory(slicer_filament="GFL99", material="PLA")
        client = _client(connected=True)

        response = await _assign(async_client, printer.id, spool.id, client)

        body = response.json()
        assert (body["configured"], body["pending_config"], body["printer_offline"]) == (True, False, False)
        client.register_assignment_verification.assert_called_once()
        row = await db_session.get(SpoolAssignment, body["id"])
        await db_session.refresh(row)
        assert row.fingerprint_type == "PLA"


class TestTheReconnect:
    def test_unchanged_ams_data_after_a_reconnect_fires_an_ams_change(self):
        client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SERIAL", access_code="code", model="X1C")
        client._client = MagicMock()
        client._client.subscribe.return_value = (0, 1)
        changes = []
        client.on_ams_change = changes.append
        frame = {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "remain": 80}]}]}

        client._handle_ams_data(frame)
        client._handle_ams_data(frame)
        assert len(changes) == 1  # unchanged data fires nothing while connected

        client._on_connect(client._client, None, {}, 0)
        client._handle_ams_data(frame)

        assert len(changes) == 2


@pytest.mark.asyncio
@pytest.mark.integration
class TestSpoolmanMode:
    async def test_nothing_after_a_refused_setting(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client
    ):
        client = _client(connected=False)
        client.printer_state = None

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm:
            pm.get_client = MagicMock(return_value=client)
            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={"spoolman_spool_id": 10, "printer_id": test_printer.id, "ams_id": 0, "tray_id": 1},
            )

        assert response.status_code == 200
        client.ams_set_filament_setting.assert_called_once()
        client.extrusion_cali_sel.assert_not_called()
        client.register_assignment_verification.assert_not_called()
