"""Which archive figures a user who may only edit their own archives can set.

The archive's cost and filament weight are what later runs of it are charged
from. Callers who may edit every archive set both; callers limited to their
own archives may fill in a weight only for an archive without its 3MF, which
has none and cannot be printed again.
"""

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

OWN = ["archives:read_own", "archives:update_own"]


async def _owner_and_archive(async_client, archive_factory, printer_factory, *, grams, cost, file_path=None):
    admin = await _admin_token(async_client)
    group = await _group(async_client, admin, "own_archives", permissions=OWN)
    jwt, user_id = await _user(async_client, admin, "owner", [group])
    printer = await printer_factory()
    extra = {} if file_path is None else {"file_path": file_path}
    archive = await archive_factory(
        printer.id, created_by_id=user_id, filament_used_grams=grams, cost=cost, filament_type="PLA", **extra
    )
    return admin, jwt, archive.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_owner_cannot_set_the_cost(async_client: AsyncClient, archive_factory, printer_factory):
    _, jwt, archive_id = await _owner_and_archive(async_client, archive_factory, printer_factory, grams=100.0, cost=2.5)

    response = await async_client.patch(f"/api/v1/archives/{archive_id}", headers=_auth(jwt), json={"cost": 0.01})

    assert response.status_code == 403
    after = await async_client.get(f"/api/v1/archives/{archive_id}", headers=_auth(jwt))
    assert after.json()["cost"] == 2.5


@pytest.mark.asyncio
@pytest.mark.integration
async def test_owner_cannot_change_an_existing_weight(async_client: AsyncClient, archive_factory, printer_factory):
    _, jwt, archive_id = await _owner_and_archive(async_client, archive_factory, printer_factory, grams=100.0, cost=2.5)

    response = await async_client.patch(
        f"/api/v1/archives/{archive_id}", headers=_auth(jwt), json={"filament_used_grams": 90000}
    )

    assert response.status_code == 403
    after = await async_client.get(f"/api/v1/archives/{archive_id}", headers=_auth(jwt))
    assert (after.json()["filament_used_grams"], after.json()["cost"]) == (100.0, 2.5)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_owner_fills_a_missing_weight(async_client: AsyncClient, archive_factory, printer_factory):
    _, jwt, archive_id = await _owner_and_archive(
        async_client, archive_factory, printer_factory, grams=None, cost=None, file_path=""
    )

    response = await async_client.patch(
        f"/api/v1/archives/{archive_id}", headers=_auth(jwt), json={"filament_used_grams": 120}
    )

    assert response.status_code == 200
    assert (response.json()["filament_used_grams"], response.json()["cost"]) == (120, 3.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_filling_the_weight_keeps_an_existing_cost(async_client: AsyncClient, archive_factory, printer_factory):
    _, jwt, archive_id = await _owner_and_archive(
        async_client, archive_factory, printer_factory, grams=None, cost=7.0, file_path=""
    )

    response = await async_client.patch(
        f"/api/v1/archives/{archive_id}", headers=_auth(jwt), json={"filament_used_grams": 120}
    )

    assert response.status_code == 200
    assert response.json()["cost"] == 7.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_owner_still_edits_everything_else(async_client: AsyncClient, archive_factory, printer_factory):
    _, jwt, archive_id = await _owner_and_archive(async_client, archive_factory, printer_factory, grams=100.0, cost=2.5)

    response = await async_client.patch(
        f"/api/v1/archives/{archive_id}",
        headers=_auth(jwt),
        json={"notes": "fine", "filament_used_grams": 100.0},
    )

    assert response.status_code == 200
    assert response.json()["notes"] == "fine"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_editing_every_archive_sets_both(async_client: AsyncClient, archive_factory, printer_factory):
    admin, _, archive_id = await _owner_and_archive(
        async_client, archive_factory, printer_factory, grams=100.0, cost=2.5
    )

    weight = await async_client.patch(
        f"/api/v1/archives/{archive_id}", headers=_auth(admin), json={"filament_used_grams": 200}
    )
    cost = await async_client.patch(f"/api/v1/archives/{archive_id}", headers=_auth(admin), json={"cost": 9.99})

    assert (weight.status_code, weight.json()["cost"]) == (200, 5.0)
    assert (cost.status_code, cost.json()["cost"]) == (200, 9.99)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_owner_cannot_fill_the_weight_of_an_archive_with_its_3mf(
    async_client: AsyncClient, archive_factory, printer_factory
):
    """It can be printed again, and those runs are charged from the weight."""
    _, jwt, archive_id = await _owner_and_archive(async_client, archive_factory, printer_factory, grams=None, cost=None)

    response = await async_client.patch(
        f"/api/v1/archives/{archive_id}", headers=_auth(jwt), json={"filament_used_grams": 0.01}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("body", [{"filament_used_grams": None}, {"cost": None}])
async def test_owner_cannot_clear_either_figure(async_client: AsyncClient, archive_factory, printer_factory, body):
    _, jwt, archive_id = await _owner_and_archive(async_client, archive_factory, printer_factory, grams=100.0, cost=2.5)

    response = await async_client.patch(f"/api/v1/archives/{archive_id}", headers=_auth(jwt), json=body)

    assert response.status_code == 403
