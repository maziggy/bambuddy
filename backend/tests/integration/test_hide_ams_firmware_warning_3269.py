"""Opt-in hiding of the AMS firmware mismatch warning, for a BMCU (#3269).

Only the 05XX_0400_0001_0044 family is ever hidden, only from hms[], and only
when the user turns the option on.
"""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from backend.app.services import hms_errors
from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.hms_errors import AMS_FIRMWARE_MISMATCH_CODES, is_hidden_hms_code
from backend.app.services.printer_manager import PrinterManager

MISMATCH_AMS_A = {"attr": 0x05000400, "code": 0x00010044}
OTHER_FAULT = {"attr": 0x07000100, "code": 0x00020001}


@pytest.fixture(autouse=True)
def reset_option():
    hms_errors.set_hide_ams_firmware_mismatch(False)
    yield
    hms_errors.set_hide_ams_firmware_mismatch(False)


@pytest.fixture
def client():
    c = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
    c.state.connected = True
    c._client = MagicMock()
    return c


def codes(c) -> set[str]:
    return {e.full_code for e in c.state.hms_errors}


class TestWhichCodes:
    def test_it_is_the_ams_a_to_d_and_ams_lite_family(self):
        assert {
            "0500040000010044",
            "0501040000010044",
            "0502040000010044",
            "0503040000010044",
            "0510040000010044",
        } == AMS_FIRMWARE_MISMATCH_CODES

    def test_nothing_is_hidden_until_the_option_is_on(self):
        for code in AMS_FIRMWARE_MISMATCH_CODES:
            assert is_hidden_hms_code(code) is False

    def test_every_code_of_the_family_is_hidden_when_it_is_on(self):
        hms_errors.set_hide_ams_firmware_mismatch(True)

        assert all(is_hidden_hms_code(code) for code in AMS_FIRMWARE_MISMATCH_CODES)

    @pytest.mark.parametrize(
        "code",
        [
            "0500050000010010",  # same wording, no report of a BMCU raising it
            "0580040000010045",  # AMS-HT
            "0500040000010046",  # Laser Module
            "0500040000010047",  # Air Pump
            "0500040000010048",  # Cutting Module
            "0504040000010044",  # a unit the family does not name
            "0500409D",  # a print error (8 characters)
            "0500400C",
            "",
            None,
        ],
    )
    def test_anything_else_stays_visible(self, code):
        hms_errors.set_hide_ams_firmware_mismatch(True)

        assert is_hidden_hms_code(code) is False

    def test_the_comparison_ignores_case(self):
        hms_errors.set_hide_ams_firmware_mismatch(True)

        assert is_hidden_hms_code("0500040000010044".lower()) is True


class TestParsing:
    def test_the_warning_is_listed_by_default(self, client):
        client._update_state({"hms": [MISMATCH_AMS_A]})

        assert codes(client) == {"0500040000010044"}

    def test_the_warning_is_dropped_when_the_option_is_on(self, client):
        hms_errors.set_hide_ams_firmware_mismatch(True)

        client._update_state({"hms": [MISMATCH_AMS_A, OTHER_FAULT]})

        assert codes(client) == {"0700010000020001"}

    def test_a_unit_other_than_ams_a_is_dropped_too(self, client):
        hms_errors.set_hide_ams_firmware_mismatch(True)

        client._update_state({"hms": [{"attr": 0x05100400, "code": 0x00010044}]})

        assert codes(client) == set()

    def test_a_print_error_with_similar_wording_is_never_hidden(self, client):
        hms_errors.set_hide_ams_firmware_mismatch(True)

        client._update_state({"print_error": 0x0500409D})

        assert any(e.full_code == "0500409D" for e in client.state.hms_errors)

    def test_turning_it_off_lists_the_warning_again_on_the_next_report(self, client):
        hms_errors.set_hide_ams_firmware_mismatch(True)
        client._update_state({"hms": [MISMATCH_AMS_A]})
        hms_errors.set_hide_ams_firmware_mismatch(False)

        client._update_state({"hms": [MISMATCH_AMS_A]})

        assert codes(client) == {"0500040000010044"}


class TestApplyingAChange:
    def make_manager(self, connected=True):
        manager = PrinterManager()
        c = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST123", access_code="12345678")
        c.state.connected = connected
        c._client = MagicMock()
        c.request_status_update = MagicMock()
        manager._clients[1] = c
        return manager, c

    def test_hiding_drops_the_listed_warning_at_once_and_keeps_other_faults(self):
        manager, c = self.make_manager()
        c._update_state({"hms": [MISMATCH_AMS_A, OTHER_FAULT]})
        hms_errors.set_hide_ams_firmware_mismatch(True)

        manager.apply_hms_filter()

        assert codes(c) == {"0700010000020001"}

    def test_a_connected_printer_is_asked_for_a_full_report(self):
        manager, c = self.make_manager()

        manager.apply_hms_filter()

        c.request_status_update.assert_called_once()

    def test_a_printer_that_is_not_connected_is_left_alone(self):
        manager, c = self.make_manager(connected=False)

        manager.apply_hms_filter()

        c.request_status_update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
class TestSetting:
    async def test_it_is_off_by_default(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/settings/")

        assert response.status_code == 200, response.text
        assert response.json()["hide_ams_firmware_warning"] is False

    async def test_saving_it_turns_the_hiding_on_and_off(self, async_client: AsyncClient):
        response = await async_client.put("/api/v1/settings/", json={"hide_ams_firmware_warning": True})

        assert response.status_code == 200, response.text
        assert response.json()["hide_ams_firmware_warning"] is True
        assert is_hidden_hms_code("0500040000010044") is True

        response = await async_client.put("/api/v1/settings/", json={"hide_ams_firmware_warning": False})

        assert response.json()["hide_ams_firmware_warning"] is False
        assert is_hidden_hms_code("0500040000010044") is False


@pytest.mark.asyncio
@pytest.mark.integration
class TestStartup:
    """The stored choice is applied when Bambuddy starts, not only when it is saved."""

    async def _store(self, db_session, value: str) -> None:
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="hide_ams_firmware_warning", value=value))
        await db_session.commit()

    async def test_a_stored_on_hides_the_warning(self, db_session):
        from backend.app.main import load_hide_ams_firmware_warning

        await self._store(db_session, "true")
        await load_hide_ams_firmware_warning(db_session)

        assert is_hidden_hms_code("0500040000010044") is True

    async def test_a_stored_off_lists_it(self, db_session):
        from backend.app.main import load_hide_ams_firmware_warning

        hms_errors.set_hide_ams_firmware_mismatch(True)
        await self._store(db_session, "false")
        await load_hide_ams_firmware_warning(db_session)

        assert is_hidden_hms_code("0500040000010044") is False

    async def test_nothing_stored_lists_it(self, db_session):
        from backend.app.main import load_hide_ams_firmware_warning

        hms_errors.set_hide_ams_firmware_mismatch(True)
        await load_hide_ams_firmware_warning(db_session)

        assert is_hidden_hms_code("0500040000010044") is False

    async def test_the_lifespan_loads_it_after_the_migrations(self):
        """Read from the source, as in test_oidc_env_startup.py: running the
        lifespan starts printers, MQTT and schedulers."""
        import inspect

        from backend.app.main import lifespan

        source = inspect.getsource(lifespan)
        call = source.index("await load_hide_ams_firmware_warning(")
        assert source.index("await init_db()") < call
