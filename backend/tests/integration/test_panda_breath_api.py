"""Panda Breath routes (#1466): the command goes to the heater bound to the
printer in the path, found by its serial number, never by a caller's id."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.panda_breath import panda_breath_service


@pytest.fixture
def bound(printer_factory):
    """A printer with a Panda Breath bound to it, on a connected broker."""
    created = []

    async def _make(**kwargs):
        printer = await printer_factory(name="P", **kwargs)
        created.append(printer)
        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            pm.printer_id_for_serial.return_value = None
            panda_breath_service.apply_message(
                "panda_breath/AABBCC/state",
                json.dumps({"chamber_temp": 40, "work_on": "ON", "printer_sn": printer.serial_number}),
            )
        return printer

    client = MagicMock()
    client.publish.return_value = MagicMock(rc=0)
    saved = (panda_breath_service.enabled, panda_breath_service.client, panda_breath_service.connected)
    panda_breath_service.enabled = True
    panda_breath_service.client = client
    panda_breath_service.connected = True
    yield _make, client
    panda_breath_service.enabled, panda_breath_service.client, panda_breath_service.connected = saved
    panda_breath_service.devices.clear()


class TestPandaBreathCommand:
    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("body", "key", "value"),
        [
            ({"work_on": False}, "work_on", "OFF"),
            ({"work_on": True}, "work_on", "ON"),
            ({"mode": "filament drying"}, "mode", "filament drying"),
            ({"target_temp": 55}, "target_temp", 55),
        ],
    )
    async def test_sends_the_change_to_the_bound_device(self, async_client: AsyncClient, bound, body, key, value):
        make, client = bound
        printer = await make()

        response = await async_client.post(f"/api/v1/printers/{printer.id}/panda-breath", json=body)

        assert response.status_code == 200
        client.publish.assert_called_once_with(
            "panda_breath/AABBCC/command", json.dumps({key: value}), qos=1, retain=False
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("body", [{}, {"work_on": True, "mode": "auto mode"}])
    async def test_exactly_one_change(self, async_client: AsyncClient, bound, body):
        make, client = bound
        printer = await make()
        response = await async_client.post(f"/api/v1/printers/{printer.id}/panda-breath", json=body)
        assert response.status_code == 400
        client.publish.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("body", [{"mode": "turbo"}, {"target_temp": 61}, {"target_temp": -1}])
    async def test_values_the_firmware_does_not_take_are_refused(self, async_client: AsyncClient, bound, body):
        make, client = bound
        printer = await make()
        response = await async_client.post(f"/api/v1/printers/{printer.id}/panda-breath", json=body)
        assert response.status_code == 422
        client.publish.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_printer_without_a_device(self, async_client: AsyncClient, bound, printer_factory):
        make, client = bound
        await make()
        other = await printer_factory(name="Other")
        response = await async_client.post(f"/api/v1/printers/{other.id}/panda-breath", json={"work_on": False})
        assert response.status_code == 404
        client.publish.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_printer(self, async_client: AsyncClient, bound):
        response = await async_client.post("/api/v1/printers/99999/panda-breath", json={"work_on": False})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_broker_down(self, async_client: AsyncClient, bound):
        make, client = bound
        printer = await make()
        panda_breath_service.connected = False
        response = await async_client.post(f"/api/v1/printers/{printer.id}/panda-breath", json={"work_on": False})
        assert response.status_code == 503
        client.publish.assert_not_called()


class TestPandaBreathInStatus:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_status_of_a_disconnected_printer_carries_it(self, async_client: AsyncClient, bound):
        """The heater runs with the printer off, so the offline card still gets it,
        matched by the stored serial number (the printer has no live connection)."""
        make, _ = bound
        printer = await make()
        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

        assert response.status_code == 200
        body = response.json()
        assert body["connected"] is False
        assert body["panda_breath"]["device_id"] == "AABBCC"
        assert body["panda_breath"]["chamber_temp"] == 40.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_settings_status_names_the_printer(self, async_client: AsyncClient, bound):
        make, _ = bound
        printer = await make()
        response = await async_client.get("/api/v1/settings/panda-breath/status")
        assert response.status_code == 200
        [row] = response.json()["devices"]
        assert (row["device_id"], row["printer_id"], row["printer_name"]) == ("AABBCC", printer.id, "P")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_settings_status_hides_printers_out_of_scope(self, async_client: AsyncClient, bound):
        from backend.app.core.auth import get_printer_scope_if_auth_enabled
        from backend.app.core.printer_scope import PrinterScope
        from backend.app.main import app

        make, _ = bound
        printer = await make()
        app.dependency_overrides[get_printer_scope_if_auth_enabled] = lambda: PrinterScope(frozenset({printer.id + 1}))
        try:
            response = await async_client.get("/api/v1/settings/panda-breath/status")
        finally:
            app.dependency_overrides.pop(get_printer_scope_if_auth_enabled, None)

        [row] = response.json()["devices"]
        assert row["bound"] is True
        assert row["printer_name"] is None


class TestPandaBreathSetting:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_saving_the_switch_reconfigures_the_service(self, async_client: AsyncClient):
        with patch("backend.app.services.panda_breath.panda_breath_service.configure") as configure:
            response = await async_client.put("/api/v1/settings/", json={"panda_breath_enabled": True})

        assert response.status_code == 200
        assert response.json()["panda_breath_enabled"] is True
        assert configure.call_args.args[0]["panda_breath_enabled"] is True
