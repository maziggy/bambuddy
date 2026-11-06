"""Chamber light for the camera (#1655): the settings, and the routes that hold the light.

The light mode and delay are set through the settings API here, so the service
reads them back from the database exactly as it does in production."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services import camera_light as light_svc

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def light_client(monkeypatch):
    client = MagicMock()
    client.state = SimpleNamespace(chamber_light=False)
    client.set_chamber_light.return_value = True
    monkeypatch.setattr(light_svc, "_get_client", lambda _pid: client)
    monkeypatch.setattr(light_svc, "_holders", {})
    monkeypatch.setattr(light_svc, "_lit_at", {})
    monkeypatch.setattr(light_svc, "_settings_cache", None)
    off_tasks: dict = {}
    monkeypatch.setattr(light_svc, "_off_tasks", off_tasks)
    yield client
    for task in off_tasks.values():
        task.cancel()


async def _set_light(async_client: AsyncClient, **values):
    response = await async_client.patch("/api/v1/settings/", json=values)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_light_is_off_by_default(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    settings = (await async_client.get("/api/v1/settings/")).json()
    assert settings["camera_light_mode"] == "off"
    assert settings["camera_light_delay"] == 2.0
    assert (await async_client.get(f"/api/v1/printers/{printer.id}")).json()["camera_light_auto"] is False


async def test_the_mode_and_delay_are_saved(async_client: AsyncClient):
    saved = await _set_light(async_client, camera_light_mode="selected", camera_light_delay=1.5)
    assert saved["camera_light_mode"] == "selected"
    assert saved["camera_light_delay"] == 1.5


async def test_a_printer_is_picked_for_the_selected_mode(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"camera_light_auto": True})
    assert response.status_code == 200
    assert response.json()["camera_light_auto"] is True


@pytest.mark.parametrize(
    "values",
    [
        {"camera_light_mode": "sometimes"},
        {"camera_light_delay": -1},
        {"camera_light_delay": 5.5},
        {"camera_light_delay": None},
    ],
)
async def test_bad_light_settings_are_refused(async_client: AsyncClient, values):
    response = await async_client.patch("/api/v1/settings/", json=values)
    assert response.status_code == 422


async def test_a_snapshot_is_taken_with_the_light_on(async_client: AsyncClient, printer_factory, light_client):
    """Home Assistant and other automations take their pictures here."""
    await _set_light(async_client, camera_light_mode="all", camera_light_delay=0)
    printer = await printer_factory()
    fake_jpeg = b"\xff\xd8\xff\xe0snap"
    seen = {}

    async def _snapshot(printer_id, _printer):
        seen["calls"] = list(light_client.set_chamber_light.call_args_list)
        from fastapi import Response

        return Response(content=fake_jpeg, media_type="image/jpeg")

    with patch("backend.app.api.routes.camera._snapshot_response", _snapshot):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")

    assert response.status_code == 200
    assert response.content == fake_jpeg
    assert [c.args for c in seen["calls"]] == [(True,)]


async def test_a_snapshot_leaves_the_light_alone_by_default(async_client: AsyncClient, printer_factory, light_client):
    printer = await printer_factory()

    async def _snapshot(printer_id, _printer):
        from fastapi import Response

        return Response(content=b"\xff\xd8", media_type="image/jpeg")

    with patch("backend.app.api.routes.camera._snapshot_response", _snapshot):
        await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")

    light_client.set_chamber_light.assert_not_called()


async def test_selected_mode_lights_only_the_picked_printers(async_client: AsyncClient, printer_factory, light_client):
    await _set_light(async_client, camera_light_mode="selected", camera_light_delay=0)
    picked = await printer_factory(camera_light_auto=True)
    other = await printer_factory(serial_number="00M09A999999999")

    async def _snapshot(printer_id, _printer):
        from fastapi import Response

        return Response(content=b"\xff\xd8", media_type="image/jpeg")

    with patch("backend.app.api.routes.camera._snapshot_response", _snapshot):
        await async_client.get(f"/api/v1/printers/{other.id}/camera/snapshot")
        light_client.set_chamber_light.assert_not_called()
        await async_client.get(f"/api/v1/printers/{picked.id}/camera/snapshot")
    light_client.set_chamber_light.assert_called_once_with(True)


async def test_saving_the_mode_applies_at_once(async_client: AsyncClient, printer_factory, light_client):
    """The service caches the settings; saving them must not wait out the cache."""
    printer = await printer_factory()

    async def _snapshot(printer_id, _printer):
        from fastapi import Response

        return Response(content=b"\xff\xd8", media_type="image/jpeg")

    with patch("backend.app.api.routes.camera._snapshot_response", _snapshot):
        await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")  # caches "off"
        await _set_light(async_client, camera_light_mode="all", camera_light_delay=0)
        await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")
    light_client.set_chamber_light.assert_called_once_with(True)


async def test_switching_the_light_by_hand_hands_it_back(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    light_svc._lit_at[printer.id] = 0.0
    client = MagicMock()
    client.set_chamber_light.return_value = True
    try:
        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_client.return_value = client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/chamber-light?on=true")
        assert response.status_code == 200
        assert printer.id not in light_svc._lit_at
    finally:
        light_svc._lit_at.pop(printer.id, None)


async def test_the_live_view_holds_the_light_while_it_streams(async_client: AsyncClient, printer_factory, light_client):
    await _set_light(async_client, camera_light_mode="all", camera_light_delay=5)
    printer = await printer_factory()
    during = {}

    async def _iter_subscriber(*_args, **_kwargs):
        during["calls"] = [c.args for c in light_client.set_chamber_light.call_args_list]
        yield b"--frame\r\n"

    broadcaster = MagicMock(subscriber_count=1)

    async def _subscribe():
        return MagicMock()

    broadcaster.subscribe = _subscribe

    async def _get_broadcaster(_key, _factory):
        return broadcaster

    with (
        patch("backend.app.api.routes.camera.get_or_create_broadcaster", _get_broadcaster),
        patch("backend.app.api.routes.camera.iter_subscriber", _iter_subscriber),
    ):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/stream")

    assert response.status_code == 200
    assert during["calls"] == [(True,)]
    # The viewer left: the light goes off after the grace, not at once.
    assert printer.id in light_svc._off_tasks


async def test_an_external_camera_view_holds_the_light_too(async_client: AsyncClient, printer_factory, light_client):
    await _set_light(async_client, camera_light_mode="selected")
    printer = await printer_factory(
        camera_light_auto=True,
        external_camera_enabled=True,
        external_camera_url="http://192.0.2.50/mjpeg",
        external_camera_type="mjpeg",
    )
    during = {}

    async def _stream(*_args, **_kwargs):
        during["calls"] = [c.args for c in light_client.set_chamber_light.call_args_list]
        yield b"--frame\r\n"

    with patch("backend.app.services.external_camera.generate_mjpeg_stream", _stream):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/stream")

    assert response.status_code == 200
    assert during["calls"] == [(True,)]
    assert printer.id in light_svc._off_tasks


async def test_a_bad_stored_value_never_blocks_saving_settings(async_client: AsyncClient, db_session):
    """The settings page sends every field back on each save. A stored mode or
    delay the update schema refuses would otherwise fail every later save."""
    from backend.app.api.routes.settings import set_setting

    await set_setting(db_session, "camera_light_mode", "sometimes")
    await set_setting(db_session, "camera_light_delay", "30")
    await db_session.commit()

    settings = (await async_client.get("/api/v1/settings/")).json()
    assert settings["camera_light_mode"] == "off"
    assert settings["camera_light_delay"] == 5.0

    resend = {**settings, "auto_archive": not settings["auto_archive"]}
    response = await async_client.put("/api/v1/settings/", json=resend)
    assert response.status_code == 200, response.text
