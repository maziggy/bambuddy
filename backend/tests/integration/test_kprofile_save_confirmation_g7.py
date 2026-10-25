"""A K-profile save says whether the printer confirmed it.

The printer acknowledges K-profile writes, but firmware that predates the ack
never answers, so a missing answer is not a refusal. The routes reported such a
write as saved all the same. They now say it was sent and not confirmed.

An edit on a single-nozzle printer deletes the profile and adds it again: if
the add did not land, the profile is gone. That edit now reads the table back.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import CALI_ACK_UNCONFIRMED, KProfile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PROFILE = {
    "slot_id": 0,
    "extruder_id": 0,
    "nozzle_id": "HS00-0.4",
    "nozzle_diameter": "0.4",
    "filament_id": "GFA00",
    "name": "Bambu PLA Basic",
    "k_value": "0.020000",
}


def _client(ack, table=None):
    client = MagicMock()
    client.state = SimpleNamespace(connected=True)
    client._is_dual_nozzle = False
    client.set_kprofile.return_value = "11"
    client.delete_kprofile.return_value = "10"
    client.set_kprofiles_batch.return_value = "12"
    client.await_cali_ack = AsyncMock(return_value=ack)
    client.get_kprofiles = AsyncMock(return_value=table or [])
    return client


def _stored(**overrides) -> KProfile:
    fields = {
        "slot_id": 4,
        "extruder_id": 0,
        "nozzle_id": "HS00-0.4",
        "nozzle_diameter": "0.4",
        "filament_id": "GFA00",
        "name": "Bambu PLA Basic",
        "k_value": "0.020000",
    }
    fields.update(overrides)
    return KProfile(**fields)


async def _post(async_client: AsyncClient, printer_factory, client, path="", body=None, method="post"):
    printer = await printer_factory(model="X1C")
    with (
        patch("backend.app.api.routes.kprofiles.printer_manager") as pm,
        patch("backend.app.api.routes.kprofiles.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        url = f"/api/v1/printers/{printer.id}/kprofiles/{path}"
        if method == "delete":
            return await async_client.request("DELETE", url, json=body)
        return await async_client.post(url, json=body)


async def test_a_confirmed_save_says_so(async_client, printer_factory):
    response = await _post(async_client, printer_factory, _client((True, "")), body=PROFILE)

    assert response.status_code == 200
    assert response.json()["confirmed"] is True


async def test_a_save_the_printer_did_not_answer_is_unconfirmed(async_client, printer_factory):
    response = await _post(async_client, printer_factory, _client((True, CALI_ACK_UNCONFIRMED)), body=PROFILE)

    assert response.status_code == 200
    assert response.json()["confirmed"] is False


@pytest.mark.parametrize(
    ("path", "body", "method"),
    [
        ("batch", [PROFILE], "post"),
        ("", {**PROFILE, "slot_id": 4, "setting_id": None}, "delete"),
    ],
)
async def test_batch_and_delete_say_it_too(async_client, printer_factory, path, body, method):
    response = await _post(
        async_client, printer_factory, _client((True, CALI_ACK_UNCONFIRMED)), path=path, body=body, method=method
    )

    assert response.status_code == 200
    assert response.json()["confirmed"] is False


class TestASingleNozzleEditIsReadBack:
    EDIT = {**PROFILE, "slot_id": 4, "k_value": "0.025000"}

    async def test_the_edited_profile_in_the_table_confirms_it(self, async_client, printer_factory):
        client = _client((True, ""), table=[_stored(slot_id=5, k_value="0.025000")])
        # The delete was answered, the add was not: the table settles it.
        client.await_cali_ack = AsyncMock(side_effect=[(True, ""), (True, CALI_ACK_UNCONFIRMED)])

        response = await _post(async_client, printer_factory, client, body=self.EDIT)

        assert response.status_code == 200
        assert response.json()["confirmed"] is True

    async def test_a_lost_profile_is_an_error(self, async_client, printer_factory):
        """The delete landed, the add did not: the table has other profiles but not this one."""
        client = _client((True, ""), table=[_stored(slot_id=2, name="Other", filament_id="GFB00")])

        response = await _post(async_client, printer_factory, client, body=self.EDIT)

        assert response.status_code == 502
        assert "not in the printer's table" in response.json()["detail"]

    async def test_the_old_value_still_in_the_table_is_an_error(self, async_client, printer_factory):
        client = _client((True, ""), table=[_stored(slot_id=4, k_value="0.020000")])

        response = await _post(async_client, printer_factory, client, body=self.EDIT)

        assert response.status_code == 502

    async def test_an_unreadable_table_leaves_it_to_the_ack(self, async_client, printer_factory):
        """An empty answer is also what a read that timed out returns."""
        client = _client((True, CALI_ACK_UNCONFIRMED), table=[])

        response = await _post(async_client, printer_factory, client, body=self.EDIT)

        assert response.status_code == 200
        assert response.json()["confirmed"] is False

    async def test_an_unanswered_delete_leaves_it_unconfirmed(self, async_client, printer_factory):
        """The edited profile landed, but the old one may still be there too."""
        client = _client((True, ""), table=[_stored(slot_id=5, k_value="0.025000")])
        client.await_cali_ack = AsyncMock(side_effect=[(True, CALI_ACK_UNCONFIRMED), (True, "")])

        response = await _post(async_client, printer_factory, client, body=self.EDIT)

        assert response.status_code == 200
        assert response.json()["confirmed"] is False
