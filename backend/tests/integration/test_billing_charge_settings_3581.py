"""The settings of the charging policy (#3581)."""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SWITCHES = ("billing_completed_energy", "billing_completed_wear", "billing_failed_energy", "billing_failed_wear")
DEFAULTS = dict.fromkeys(SWITCHES, False) | {"billing_failed_filament": "proportional"}


async def _policy(async_client: AsyncClient) -> dict:
    settings = (await async_client.get("/api/v1/settings/")).json()
    return {key: settings[key] for key in DEFAULTS}


async def test_the_defaults_are_the_charge_as_it_was(async_client: AsyncClient):
    assert await _policy(async_client) == DEFAULTS


@pytest.mark.parametrize("key", SWITCHES)
async def test_each_switch_is_saved_and_read_back_on_its_own(async_client: AsyncClient, key: str):
    assert (await async_client.put("/api/v1/settings/", json={key: True})).status_code == 200
    assert await _policy(async_client) == DEFAULTS | {key: True}

    await async_client.put("/api/v1/settings/", json={key: False})
    assert await _policy(async_client) == DEFAULTS


@pytest.mark.parametrize("mode", ["full", "none", "proportional"])
async def test_the_filament_mode_is_saved_and_read_back(async_client: AsyncClient, mode: str):
    assert (await async_client.put("/api/v1/settings/", json={"billing_failed_filament": mode})).status_code == 200
    assert await _policy(async_client) == DEFAULTS | {"billing_failed_filament": mode}


@pytest.mark.parametrize("bad", [{key: "sometimes"} for key in SWITCHES] + [{"billing_failed_filament": "double"}])
async def test_a_value_it_does_not_know_is_refused(async_client: AsyncClient, bad: dict):
    assert (await async_client.put("/api/v1/settings/", json=bad)).status_code == 422
    assert await _policy(async_client) == DEFAULTS


async def test_the_filament_mode_cannot_be_cleared(async_client: AsyncClient):
    assert (await async_client.put("/api/v1/settings/", json={"billing_failed_filament": None})).status_code == 422


async def test_what_is_saved_is_what_charges_read(async_client: AsyncClient, db_session):
    from backend.app.services.finance_billing import print_charge_policy

    await async_client.put(
        "/api/v1/settings/",
        json={
            "billing_enabled": True,
            "billing_completed_wear": True,
            "billing_failed_filament": "full",
            "billing_failed_energy": True,
        },
    )

    assert await print_charge_policy(db_session, "completed") == ("proportional", False, True)
    assert await print_charge_policy(db_session, "failed") == ("full", True, False)
