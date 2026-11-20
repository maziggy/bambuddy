"""Printer prompt sounds (start / finish music, alerts) from Bambuddy (#3483).

Read from home_flag (bit 18: the printer has the option, bit 17: it is on),
set with print.print_option sound_enable, offered only when the printer reports
the option. The home_flag values are the ones the issue captured on an X2D.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, parse_sound_from_home_flag

SOUND_OFF = -1067098728  # bit 18 set, bit 17 clear
SOUND_ON = -1066967656  # bits 18 and 17 set
NO_OPTION = 0xC065CD98 & ~(1 << 18)  # a printer that does not have it


@pytest.fixture
def client():
    c = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
    c.state.connected = True
    c._client = MagicMock()
    return c


class TestParsing:
    def test_the_captured_values(self):
        assert parse_sound_from_home_flag(SOUND_OFF) == (True, False)
        assert parse_sound_from_home_flag(SOUND_ON) == (True, True)

    def test_a_printer_without_the_option_is_not_supported(self):
        supported, _ = parse_sound_from_home_flag(NO_OPTION)
        assert supported is False

    @pytest.mark.parametrize("value", [None, "12", 1.5, True, [1]])
    def test_a_value_that_is_not_an_integer_says_nothing(self, value):
        assert parse_sound_from_home_flag(value) is None

    def test_the_state_follows_the_frames(self, client):
        assert (client.state.sound_supported, client.state.sound_enabled) == (False, False)

        client._update_state({"home_flag": SOUND_OFF})
        assert (client.state.sound_supported, client.state.sound_enabled) == (True, False)

        client._update_state({"home_flag": SOUND_ON})
        assert (client.state.sound_supported, client.state.sound_enabled) == (True, True)

    def test_a_frame_without_home_flag_changes_nothing(self, client):
        client._update_state({"home_flag": SOUND_ON})

        client._update_state({"gcode_state": "IDLE"})

        assert client.state.sound_enabled is True


class TestSetting:
    def test_it_publishes_the_print_option_command(self, client):
        assert client.set_sound_enable(True) is True

        topic, payload = client._client.publish.call_args.args[:2]
        assert topic == client.topic_publish
        assert json.loads(payload)["print"] == {
            "command": "print_option",
            "sequence_id": json.loads(payload)["print"]["sequence_id"],
            "sound_enable": True,
        }

    def test_it_sends_off_as_off(self, client):
        client.set_sound_enable(False)

        assert json.loads(client._client.publish.call_args.args[1])["print"]["sound_enable"] is False

    def test_it_does_nothing_while_not_connected(self, client):
        client.state.connected = False

        assert client.set_sound_enable(True) is False
        client._client.publish.assert_not_called()

    def test_the_state_follows_at_once(self, client):
        client._update_state({"home_flag": SOUND_OFF})

        client.set_sound_enable(True)

        assert client.state.sound_enabled is True

    def test_a_stale_frame_during_the_hold_does_not_flip_it_back(self, client):
        client._update_state({"home_flag": SOUND_OFF})
        client.set_sound_enable(True)

        client._update_state({"home_flag": SOUND_OFF})  # the printer has not applied it yet

        assert client.state.sound_enabled is True

    def test_support_is_still_read_during_the_hold(self, client):
        client.set_sound_enable(True)

        client._update_state({"home_flag": SOUND_OFF})

        assert client.state.sound_supported is True

    def test_after_the_hold_the_printer_is_authoritative(self, client):
        client._update_state({"home_flag": SOUND_OFF})
        client.set_sound_enable(True)
        client._xcam_hold_start["print_option_sound_enable"] = time.time() - 10.0

        client._update_state({"home_flag": SOUND_OFF})

        assert client.state.sound_enabled is False

    def test_a_confirming_frame_during_the_hold_keeps_it(self, client):
        client.set_sound_enable(True)

        client._update_state({"home_flag": SOUND_ON})

        assert client.state.sound_enabled is True


class TestApi:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_missing_printer_is_404(self, async_client):
        response = await async_client.post("/api/v1/printers/99999/sound?enabled=true")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_printer_that_is_not_connected_is_refused(self, async_client, printer_factory):
        printer = await printer_factory()

        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/sound?enabled=true")

        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_printer_without_the_option_is_refused(self, async_client, printer_factory):
        printer = await printer_factory()
        mock_client = MagicMock()
        mock_client.state = SimpleNamespace(connected=True, sound_supported=False)

        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/sound?enabled=true")

        assert response.status_code == 400
        mock_client.set_sound_enable.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_it_sends_the_command(self, async_client, printer_factory, enabled):
        printer = await printer_factory()
        mock_client = MagicMock()
        mock_client.state = SimpleNamespace(connected=True, sound_supported=True)
        mock_client.set_sound_enable.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/sound?enabled={str(enabled).lower()}")

        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "sound_enabled": enabled}
        mock_client.set_sound_enable.assert_called_once_with(enabled)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_send_that_fails_is_reported(self, async_client, printer_factory):
        printer = await printer_factory()
        mock_client = MagicMock()
        mock_client.state = SimpleNamespace(connected=True, sound_supported=True)
        mock_client.set_sound_enable.return_value = False

        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/sound?enabled=true")

        assert response.status_code == 500


class TestStatusDict:
    def test_the_option_is_in_the_status_only_when_the_printer_has_it(self):
        from backend.app.services.printer_manager import printer_state_to_dict

        def dict_for(supported, enabled):
            c = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
            c.state.sound_supported, c.state.sound_enabled = supported, enabled
            return printer_state_to_dict(c.state)

        assert dict_for(False, False)["sound_enabled"] is None
        assert dict_for(True, False)["sound_enabled"] is False
        assert dict_for(True, True)["sound_enabled"] is True
