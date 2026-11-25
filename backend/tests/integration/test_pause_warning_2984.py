"""The pause heads-up: its channel toggle, its setting, and the time on the status (#2984)."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_channel_toggle_saves_on_create_and_update(async_client: AsyncClient):
    data = {
        "name": "Pause heads-up",
        "provider_type": "ntfy",
        "config": {"server": "https://ntfy.sh", "topic": "test"},
        "on_print_pause_upcoming": True,
    }
    created = await async_client.post("/api/v1/notifications/", json=data)
    assert created.status_code == 200
    assert created.json()["on_print_pause_upcoming"] is True

    provider_id = created.json()["id"]
    updated = await async_client.patch(f"/api/v1/notifications/{provider_id}", json={"on_print_pause_upcoming": False})
    assert updated.status_code == 200
    assert updated.json()["on_print_pause_upcoming"] is False

    listed = await async_client.get("/api/v1/notifications/")
    assert next(p for p in listed.json() if p["id"] == provider_id)["on_print_pause_upcoming"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_channel_toggle_off_by_default(async_client: AsyncClient):
    data = {"name": "Plain", "provider_type": "ntfy", "config": {"server": "https://ntfy.sh", "topic": "t"}}
    created = await async_client.post("/api/v1/notifications/", json=data)
    assert created.json()["on_print_pause_upcoming"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_heads_up_minutes_setting(async_client: AsyncClient):
    assert (await async_client.get("/api/v1/settings")).json()["pause_warning_minutes"] == 5

    saved = await async_client.patch("/api/v1/settings", json={"pause_warning_minutes": 15})
    assert saved.status_code == 200
    assert (await async_client.get("/api/v1/settings")).json()["pause_warning_minutes"] == 15

    for bad in (0, 121):
        assert (await async_client.patch("/api/v1/settings", json={"pause_warning_minutes": bad})).status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_heads_up_reads_the_saved_setting(async_client: AsyncClient, db_session):
    from backend.app.services.pause_eta import warning_minutes

    await async_client.patch("/api/v1/settings", json={"pause_warning_minutes": 20})
    assert await warning_minutes(db_session) == 20


@pytest.mark.asyncio
@pytest.mark.integration
async def test_status_reports_the_next_pause(async_client: AsyncClient, printer_factory):
    from backend.app.services.bambu_mqtt import PrinterState
    from backend.app.utils.threemf_tools import PausePoint

    printer = await printer_factory()
    state = PrinterState()
    state.connected = True
    state.state = "RUNNING"
    state.layer_num = 1
    state.remaining_time = 110
    state.pause_points = [PausePoint(3, 78)]

    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_status = MagicMock(return_value=state)
        mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)
        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

    assert response.status_code == 200
    assert response.json()["next_pause"] == {"layer": 3, "minutes": 32}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unparseable_stored_value_reads_back_as_the_default(async_client: AsyncClient, db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="pause_warning_minutes", value="None"))
    await db_session.commit()

    response = await async_client.get("/api/v1/settings")
    assert response.status_code == 200
    assert response.json()["pause_warning_minutes"] == 5
