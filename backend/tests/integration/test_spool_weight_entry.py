"""A weight entered for a new spool is kept, and a negative one is refused.

Creating a spool (one, several, or from a CSV) left ``weight_locked`` off even
when a used weight was entered, so the AMS remain% sync could overwrite a
part-used spool's weight on its first pass; editing a spool's weight locks it.
A weight above 0 entered at creation is now locked too. And ``weight_used`` had
no lower bound in the API: a negative value gave a spool more than its label.
Print tracking may still take a spool past its label (mis-labelled spools).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.spool import Spool
from backend.tests.integration.test_inventory_csv import _csv_upload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

BASE = {"material": "PLA", "brand": "Bambu", "color_name": "White", "rgba": "FFFFFFFF", "label_weight": 1000}


async def _spools(db_session):
    db_session.expire_all()
    return (await db_session.execute(select(Spool).order_by(Spool.id))).scalars().all()


@pytest.mark.parametrize(("weight_used", "locked"), [(250, True), (0, False)])
async def test_a_new_spool_keeps_the_weight_entered(async_client: AsyncClient, weight_used, locked):
    response = await async_client.post("/api/v1/inventory/spools", json={**BASE, "weight_used": weight_used})

    assert response.status_code == 200, response.text
    assert response.json()["weight_locked"] is locked


async def test_an_explicit_unlock_is_respected(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/inventory/spools", json={**BASE, "weight_used": 250, "weight_locked": False}
    )

    assert response.json()["weight_locked"] is False


async def test_copies_keep_the_weight_entered(async_client: AsyncClient, db_session):
    response = await async_client.post(
        "/api/v1/inventory/spools/bulk", json={"spool": {**BASE, "weight_used": 400}, "quantity": 2}
    )

    assert response.status_code == 200, response.text
    assert [s.weight_locked for s in await _spools(db_session)] == [True, True]


async def test_an_imported_weight_is_kept(async_client: AsyncClient, db_session):
    csv_text = "material,brand,color_name,weight_used\nPLA,Bambu,White,300\nPETG,Bambu,Black,0\n"

    response = await async_client.post("/api/v1/inventory/spools/import", files=_csv_upload(csv_text))

    assert response.status_code == 200, response.text
    by_material = {s.material: s.weight_locked for s in await _spools(db_session)}
    assert by_material == {"PLA": True, "PETG": False}


async def test_a_negative_weight_is_refused(async_client: AsyncClient):
    created = await async_client.post("/api/v1/inventory/spools", json={**BASE, "weight_used": -10})
    assert created.status_code == 422

    spool = (await async_client.post("/api/v1/inventory/spools", json=BASE)).json()
    edited = await async_client.patch(f"/api/v1/inventory/spools/{spool['id']}", json={"weight_used": -1})
    assert edited.status_code == 422


async def test_more_than_the_label_is_still_taken(async_client: AsyncClient):
    """A mis-labelled spool really can give more than its label."""
    response = await async_client.post("/api/v1/inventory/spools", json={**BASE, "weight_used": 1100})

    assert response.status_code == 200, response.text


async def test_a_negative_weight_stored_before_still_lists(async_client: AsyncClient, db_session):
    db_session.add(Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=-20))
    await db_session.commit()

    response = await async_client.get("/api/v1/inventory/spools")

    assert response.status_code == 200, response.text
