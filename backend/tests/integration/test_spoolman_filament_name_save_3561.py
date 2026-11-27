"""Saving a Spoolman spool leaves the filament's name alone (#3561).

The update route rebuilt the filament's name as "<material> <subtype>" and
PATCHed it whenever that differed from the stored one -- so saving any change
to a spool whose filament is called "Matte Scarlet Red" renamed it in Spoolman.
With the name now read as subtype "Matte" and colour "Scarlet Red", the same
rule would have cut it down to "PLA Matte" and lost the colour from the name.
The name is only rebuilt when material or subtype is actually changed.
"""

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_spoolman_inventory_api import (  # noqa: F401  (fixtures)
    SAMPLE_SPOOLMAN_SPOOL,
    mock_spoolman_client,
    spoolman_settings,
)

PRODUCT_NAMED = {
    **SAMPLE_SPOOLMAN_SPOOL,
    "filament": {**SAMPLE_SPOOLMAN_SPOOL["filament"], "name": "Matte Scarlet Red", "material": "PLA"},
}


@pytest.fixture
def product_named(mock_spoolman_client):  # noqa: F811
    mock_spoolman_client.get_spool.return_value = PRODUCT_NAMED
    return mock_spoolman_client


@pytest.mark.asyncio
@pytest.mark.integration
async def test_saving_the_form_unchanged_does_not_rename_the_filament(
    async_client: AsyncClient,
    spoolman_settings,  # noqa: F811
    product_named,
):
    """What the form sends back: the subtype and colour it was shown."""
    response = await async_client.patch(
        "/api/v1/spoolman/inventory/spools/42",
        json={"material": "PLA", "subtype": "Matte", "color_name": "Scarlet Red", "note": "moved to shelf 2"},
    )

    assert response.status_code == 200
    product_named.patch_filament.assert_not_called()
    product_named.find_or_create_filament.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_colour_change_keeps_the_name_too(
    async_client: AsyncClient,
    spoolman_settings,  # noqa: F811
    product_named,
):
    response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"rgba": "0000FFFF"})

    assert response.status_code == 200
    product_named.patch_filament.assert_called_once()
    assert product_named.patch_filament.call_args.args[1]["name"] == "Matte Scarlet Red"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_changing_the_subtype_still_renames_it(
    async_client: AsyncClient,
    spoolman_settings,  # noqa: F811
    product_named,
):
    response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"subtype": "Silk"})

    assert response.status_code == 200
    product_named.patch_filament.assert_called_once()
    assert product_named.patch_filament.call_args.args[1]["name"] == "PLA Silk"
